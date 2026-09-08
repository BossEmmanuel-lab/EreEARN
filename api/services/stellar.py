"""
Stellar / Soroban integration service for ÉréEARN.

Handles:
- Wallet-based authentication (challenge/verify)
- Soroban smart contract & Horizon escrow transactions (fund, approve_and_pay, expire_and_refund)
- Horizon and Soroban RPC status queries
- Automatic Testnet account funding and balance checks via Friendbot
- Unsigned XDR construction for Freighter/Rabet and secret-key signing

## Soroban transaction lifecycle
Soroban (InvokeHostFunction) transactions CANNOT be submitted to Horizon.
The correct flow is:
  1. Build the raw transaction with append_invoke_contract_function_op()
  2. Simulate + prepare via SorobanServer.prepare_transaction()
     – adds footprint, auth entries, and accurate resource fee
  3. Sign the prepared envelope
  4. Submit via SorobanServer.send_transaction()  (async, returns PENDING)
  5. Poll SorobanServer.get_transaction() until SUCCESS or FAILED
Classic payment transactions still use Horizon directly.
"""

import base64
import hashlib
import logging
import secrets
import threading
import time
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from stellar_sdk import (
    Asset,
    Keypair,
    Network,
    Server,
    SorobanServer,
    TransactionBuilder,
    TransactionEnvelope,
    scval,
    xdr as stellar_xdr,
)
from stellar_sdk.exceptions import BadRequestError, NotFoundError, PrepareTransactionException
from stellar_sdk.operation import InvokeHostFunction
from stellar_sdk.soroban_rpc import GetTransactionStatus, SendTransactionStatus

logger = logging.getLogger(__name__)

CHALLENGE_TTL = 300  # 5 minutes

# How long to wait for a Soroban transaction to be confirmed (seconds)
SOROBAN_POLL_TIMEOUT = 60
SOROBAN_POLL_INTERVAL = 3

_CACHED_ESCROW_KEYPAIR = None
_ESCROW_KEYPAIR_LOCK = threading.Lock()  # thread-safe caching


# Configuration helpers

def _get_horizon_server() -> Server:
    """Return a configured Horizon server instance."""
    return Server(horizon_url=settings.STELLAR_HORIZON_URL)


def _get_soroban_server() -> SorobanServer:
    """Return a configured Soroban RPC server instance."""
    return SorobanServer(server_url=settings.SOROBAN_RPC_URL)


def _get_network_passphrase() -> str:
    """Return the network passphrase for the current environment."""
    return settings.STELLAR_NETWORK_PASSPHRASE


def _is_testnet() -> bool:
    """Return True when running against Stellar Testnet."""
    network = getattr(settings, "STELLAR_NETWORK", "").upper()
    if network == "TESTNET":
        return True
    if network and network != "":
        return False
    return "testnet" in getattr(settings, "STELLAR_HORIZON_URL", "").lower()


def _is_soroban_transaction(xdr: str) -> bool:
    """Return True when the XDR envelope contains an InvokeHostFunction operation.

    Soroban transactions must be submitted via SorobanRPC, not Horizon.
    Horizon will reject them with tx_malformed if you try.
    """
    try:
        te = TransactionEnvelope.from_xdr(xdr, _get_network_passphrase())
        return any(isinstance(op, InvokeHostFunction) for op in te.transaction.operations)
    except Exception:
        return False


# Account and balance helpers

def _ensure_account_has_balance(public_key: str, min_xlm: Decimal = Decimal("100")):
    """
    Ensure an account exists on Stellar Testnet and holds enough XLM.
    Automatically tops up via Friendbot when below threshold.
    Only runs on Testnet (guarded by _is_testnet()).
    """
    if not public_key or not public_key.startswith("G") or len(public_key) != 56:
        return

    if not _is_testnet():
        return

    server = _get_horizon_server()
    try:
        acc = server.load_account(public_key)
        # stellar-sdk v11: balance data lives in acc.raw_data, not acc.balances
        raw_balances = acc.raw_data.get("balances", [])
        native_bal = Decimal("0")
        for b in raw_balances:
            if b.get("asset_type") == "native":
                native_bal = Decimal(b.get("balance", "0"))
                break
        if native_bal < (min_xlm + Decimal("10")):
            logger.info(
                f"Account {public_key} has {native_bal} XLM (need {min_xlm}). Topping up via Friendbot..."
            )
            req = urllib.request.Request(
                f"https://friendbot.stellar.org?addr={public_key}",
                headers={"User-Agent": "ÉréEARN/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    time.sleep(2)
    except NotFoundError:
        logger.info(f"Funding newly connected account {public_key} via Friendbot...")
        try:
            req = urllib.request.Request(
                f"https://friendbot.stellar.org?addr={public_key}",
                headers={"User-Agent": "ÉréEARN/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    time.sleep(2)
        except Exception as e:
            logger.warning(f"Friendbot funding failed for {public_key}: {e}")
    except Exception as e:
        logger.warning(f"Balance check error for {public_key}: {e}")


def _ensure_account_exists_on_testnet(public_key: str):
    """Ensure an account exists on testnet with at least base reserve."""
    _ensure_account_has_balance(public_key, min_xlm=Decimal("5"))


def _get_escrow_keypair() -> Keypair:
    """
    Return the escrow account keypair (server-side signer).
    Thread-safe via a module-level lock.
    """
    global _CACHED_ESCROW_KEYPAIR
    if _CACHED_ESCROW_KEYPAIR is not None:
        return _CACHED_ESCROW_KEYPAIR

    with _ESCROW_KEYPAIR_LOCK:
        if _CACHED_ESCROW_KEYPAIR is not None:
            return _CACHED_ESCROW_KEYPAIR

        secret = getattr(settings, "STELLAR_ESCROW_SECRET", "")
        if not secret:
            if getattr(settings, "DEBUG", False):
                seed = hashlib.sha256(f"ÉréEARN-escrow-seed:{settings.SECRET_KEY}".encode()).digest()
                kp = Keypair.from_raw_ed25519_seed(seed)
                _ensure_account_has_balance(kp.public_key, Decimal("1000"))
                _CACHED_ESCROW_KEYPAIR = kp
                return kp
            raise ImproperlyConfigured("STELLAR_ESCROW_SECRET environment variable is required")

        try:
            kp = Keypair.from_secret(secret)
            _ensure_account_has_balance(kp.public_key, Decimal("1000"))
            _CACHED_ESCROW_KEYPAIR = kp
            return kp
        except Exception as e:
            raise ImproperlyConfigured(f"Invalid STELLAR_ESCROW_SECRET: {e}")


# Cache token SAC addresses to avoid recomputing on every transaction
_TOKEN_SAC_CACHE: dict = {}


def _get_token_address(asset_code: str) -> str:
    """Return the Soroban Stellar Asset Contract (SAC) address for XLM or USDC.

    IMPORTANT: The SAC address is NOT the same as the escrow contract address.
    The SAC is a separate contract that wraps a classic Stellar asset (XLM, USDC)
    for use inside Soroban. It is derived from the asset + network passphrase.

    Previously this function was incorrectly returning SOROBAN_CONTRACT_ID (the
    escrow contract) as the XLM token address, which caused the escrow contract
    to reject every create_bounty call with InvalidToken.
    """
    global _TOKEN_SAC_CACHE
    if asset_code in _TOKEN_SAC_CACHE:
        return _TOKEN_SAC_CACHE[asset_code]

    network_passphrase = _get_network_passphrase()

    if asset_code == "XLM":
        # Allow explicit override via settings (e.g. for custom networks)
        override = getattr(settings, "SOROBAN_XLM_CONTRACT_ID", "") or ""
        if override:
            _TOKEN_SAC_CACHE[asset_code] = override
            return override
        # Derive the native XLM SAC address deterministically from the network passphrase
        xlm_sac = Asset.native().contract_id(network_passphrase)
        logger.debug(f"Derived XLM SAC address: {xlm_sac}")
        _TOKEN_SAC_CACHE[asset_code] = xlm_sac
        return xlm_sac

    elif asset_code == "USDC":
        # Allow explicit override
        usdc_contract = getattr(settings, "SOROBAN_USDC_CONTRACT_ID", "") or ""
        if usdc_contract:
            _TOKEN_SAC_CACHE[asset_code] = usdc_contract
            return usdc_contract
        # Derive USDC SAC from the configured issuer
        issuer = getattr(settings, "USDC_ASSET_ISSUER", "") or ""
        usdc_code = getattr(settings, "USDC_ASSET_CODE", "USDC") or "USDC"
        if not issuer:
            raise ImproperlyConfigured(
                "Either SOROBAN_USDC_CONTRACT_ID or USDC_ASSET_ISSUER must be set to use USDC."
            )
        try:
            usdc_sac = Asset(usdc_code, issuer).contract_id(network_passphrase)
            logger.debug(f"Derived USDC SAC address: {usdc_sac}")
            _TOKEN_SAC_CACHE[asset_code] = usdc_sac
            return usdc_sac
        except Exception as e:
            raise ImproperlyConfigured(
                f"Could not derive USDC SAC address from issuer {issuer!r}: {e}. "
                "Set SOROBAN_USDC_CONTRACT_ID explicitly instead."
            )

    raise ValueError(f"Unsupported asset: {asset_code}")


def _get_escrow_address() -> str:
    """
    Return the canonical escrow/contract address for transaction record keeping.
    Prefers the Soroban contract ID, then STELLAR_ESCROW_PUBLIC, then derives
    it from the escrow keypair.
    """
    addr = (
        getattr(settings, "SOROBAN_CONTRACT_ID", "") or
        getattr(settings, "STELLAR_ESCROW_PUBLIC", "")
    )
    if addr and not addr.startswith("S"):
        return addr
    try:
        return _get_escrow_keypair().public_key
    except Exception:
        return "ESCROW_CONTRACT_TESTNET"


# Soroban transaction helpers

def _prepare_soroban_transaction(tx: TransactionEnvelope) -> TransactionEnvelope:
    """
    Simulate and prepare a Soroban transaction using SorobanRPC.

    This step is MANDATORY for InvokeHostFunction transactions. It:
    - Simulates the transaction to get the Wasm footprint and auth entries
    - Calculates the accurate resource fee
    - Returns a new TransactionEnvelope ready to be signed

    Without this step, Horizon will reject the transaction with tx_malformed.
    """
    soroban = _get_soroban_server()
    try:
        prepared = soroban.prepare_transaction(tx)
        logger.debug("Soroban transaction prepared successfully")
        return prepared
    except PrepareTransactionException as e:
        error_detail = str(e)
        sim_resp = getattr(e, "simulate_transaction_response", None)
        if sim_resp and getattr(sim_resp, "error", None):
            error_detail = f"{error_detail}: {sim_resp.error}"
        logger.error(f"Soroban simulation/preparation failed: {error_detail}")
        raise RuntimeError(f"Soroban simulation failed: {error_detail}") from e
    except Exception as e:
        logger.error(f"Soroban simulation/preparation failed: {e!r}")
        raise


def get_onchain_bounty_count() -> int:
    """Query the current bounty counter from the deployed Soroban escrow contract."""
    contract_id = getattr(settings, "SOROBAN_CONTRACT_ID", "")
    if not contract_id:
        return 0
    try:
        escrow_kp = _get_escrow_keypair()
        horizon = _get_horizon_server()
        soroban = _get_soroban_server()
        account = horizon.load_account(escrow_kp.public_key)
        builder = TransactionBuilder(
            source_account=account,
            network_passphrase=_get_network_passphrase(),
            base_fee=100,
        )
        builder.append_invoke_contract_function_op(
            contract_id=contract_id,
            function_name="get_bounty_count",
            parameters=[],
        )
        builder.set_timeout(300)
        sim = soroban.simulate_transaction(builder.build())
        if sim.results and sim.results[0].xdr:
            sc_val = stellar_xdr.SCVal.from_xdr(sim.results[0].xdr)
            if hasattr(sc_val, "u64") and sc_val.u64:
                return sc_val.u64.uint64
    except Exception as e:
        logger.debug(f"get_onchain_bounty_count error: {e}")
    return 0


def _submit_soroban_transaction(signed_xdr: str) -> dict:
    """
    Submit a signed Soroban transaction via SorobanRPC and poll for the result.

    Soroban transactions are asynchronous:
      - send_transaction() returns PENDING immediately
      - We must poll get_transaction() until SUCCESS or FAILED
    This is fundamentally different from Horizon's synchronous submission.
    """
    soroban = _get_soroban_server()

    try:
        send_resp = soroban.send_transaction(signed_xdr)
    except Exception as e:
        logger.error(f"SorobanRPC send_transaction error: {e}")
        return {"successful": False, "hash": "", "result": {"error": str(e)}}

    tx_hash = send_resp.hash
    logger.info(f"Soroban tx submitted, hash={tx_hash}, initial status={send_resp.status}")

    # Immediate terminal error
    if send_resp.status == SendTransactionStatus.ERROR:
        error_detail = getattr(send_resp, "error_result_xdr", None) or str(send_resp)
        logger.error(f"SorobanRPC returned ERROR for tx {tx_hash}: {error_detail}")
        return {
            "successful": False,
            "hash": tx_hash,
            "result": {"error": "Transaction rejected by Soroban RPC", "detail": error_detail},
        }

    # TRY_AGAIN_LATER → network overloaded; treat as failure for now
    if send_resp.status == SendTransactionStatus.TRY_AGAIN_LATER:
        return {
            "successful": False,
            "hash": tx_hash,
            "result": {"error": "Soroban RPC busy — try again shortly"},
        }

    # PENDING or DUPLICATE → poll until finalized
    deadline = time.time() + SOROBAN_POLL_TIMEOUT
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        time.sleep(SOROBAN_POLL_INTERVAL)
        try:
            get_resp = soroban.get_transaction(tx_hash)
            logger.debug(f"Poll #{attempt} for {tx_hash}: status={get_resp.status}")

            if get_resp.status == GetTransactionStatus.SUCCESS:
                logger.info(f"Soroban tx {tx_hash} confirmed SUCCESS")
                return {"successful": True, "hash": tx_hash, "result": get_resp}

            if get_resp.status == GetTransactionStatus.FAILED:
                error_detail = getattr(get_resp, "result_xdr", None) or str(get_resp)
                logger.error(f"Soroban tx {tx_hash} FAILED: {error_detail}")
                return {
                    "successful": False,
                    "hash": tx_hash,
                    "result": {"error": "Soroban transaction failed", "detail": error_detail},
                }
            # NOT_FOUND → still pending, keep polling

        except Exception as e:
            logger.warning(f"Error polling Soroban tx {tx_hash} (attempt {attempt}): {e}")

    logger.error(f"Soroban tx {tx_hash} timed out after {SOROBAN_POLL_TIMEOUT}s")
    return {
        "successful": False,
        "hash": tx_hash,
        "result": {"error": f"Timed out waiting for Soroban confirmation after {SOROBAN_POLL_TIMEOUT}s"},
    }


# Wallet authentication

def generate_challenge(wallet_address: str) -> dict:
    """Generate a nonce-based challenge for wallet authentication."""
    nonce = secrets.token_hex(32)
    message = f"ÉréEARN-auth:{wallet_address}:{nonce}"
    challenge_hash = hashlib.sha256(message.encode()).hexdigest()

    cache_key = f"auth_challenge:{wallet_address}"
    expires_at = int(time.time()) + CHALLENGE_TTL
    cache.set(
        cache_key,
        {"message": message, "challenge": challenge_hash},
        timeout=CHALLENGE_TTL,
    )

    return {
        "challenge": challenge_hash,
        "message": message,
        "expires_at": expires_at,
    }


def _decode_signature(signature_str: str) -> bytes:
    """Decode signature from hex, base64, or urlsafe base64 into raw 64 bytes."""
    if not signature_str or not isinstance(signature_str, str):
        return b""

    clean_str = signature_str.strip().strip('"').strip("'")
    if clean_str.startswith("0x") or clean_str.startswith("0X"):
        clean_str = clean_str[2:]

    # Try hex (standard Ed25519 signature is 64 bytes -> 128 hex chars)
    if len(clean_str) == 128:
        try:
            return bytes.fromhex(clean_str)
        except (ValueError, TypeError):
            pass

    # Try base64 (standard Ed25519 signature in base64 is 86-88 chars)
    try:
        decoded = base64.b64decode(clean_str)
        if len(decoded) == 64:
            return decoded
    except Exception:
        pass

    # Try urlsafe base64
    try:
        padding = "=" * ((4 - len(clean_str) % 4) % 4)
        decoded = base64.urlsafe_b64decode(clean_str + padding)
        if len(decoded) == 64:
            return decoded
    except Exception:
        pass

    # Fallback to general hex
    try:
        decoded = bytes.fromhex(clean_str)
        if len(decoded) == 64:
            return decoded
    except Exception:
        pass

    return b""


def verify_challenge(wallet_address: str, signature: str) -> bool:
    """Verify that the client signed the challenge message across various wallet encoding standards."""
    cache_key = f"auth_challenge:{wallet_address}"
    cached_data = cache.get(cache_key)
    if cached_data is None:
        logger.warning(f"verify_challenge: No cached challenge found for {wallet_address}")
        return False

    message = cached_data.get("message", "")
    challenge_hash = cached_data.get("challenge", "")

    sig_bytes = _decode_signature(signature)
    if not sig_bytes:
        logger.warning(f"verify_challenge: Unable to decode signature bytes from provided signature")
        return False

    try:
        kp = Keypair.from_public_key(wallet_address)
    except Exception as e:
        logger.warning(f"verify_challenge: Invalid public key {wallet_address}: {e}")
        return False

    sep53_prefix = b"Stellar Signed Message:\n"
    msg_bytes = message.encode("utf-8")
    hash_bytes = challenge_hash.encode("utf-8")

    # Candidates that might have been signed across different wallet extensions:
    # 1. SEP-0053 Canonical Hash: sha256("Stellar Signed Message:\n" + message)
    #    This is the official standard used by Freighter, stellar-wallets-kit, and stellar-sdk.
    # 2. SEP-0053 on challenge hash: sha256("Stellar Signed Message:\n" + challenge_hash)
    # 3. Direct SHA-256 of message: sha256(message)
    # 4. Prefixed variants and raw byte variations
    candidates = [
        # Canonical SEP-0053 (Freighter & stellar-wallets-kit)
        hashlib.sha256(sep53_prefix + msg_bytes).digest(),
        hashlib.sha256(sep53_prefix + hash_bytes).digest(),
        hashlib.sha256(msg_bytes).digest(),
        hashlib.sha256(b"stellar.sep.0053\n" + msg_bytes).digest(),
        hashlib.sha256(b"\x19Stellar Signed Message:\n" + msg_bytes).digest(),
        hashlib.sha256(f"Stellar Signed Message:\n{len(message)}\n{message}".encode("utf-8")).digest(),
        # Unhashed prefixed candidates (for wallets signing raw prefixed payload)
        sep53_prefix + msg_bytes,
        sep53_prefix + hash_bytes,
        b"stellar.sep.0053\n" + msg_bytes,
        f"Stellar Signed Message:\n{message}".encode("utf-8"),
        f"Stellar Signed Message:\n{len(message)}\n{message}".encode("utf-8"),
        f"\x19Stellar Signed Message:\n{message}".encode("utf-8"),
        f"\x19Stellar Signed Message:\n{len(message)}{message}".encode("utf-8"),
        # Raw candidates
        msg_bytes,
        hash_bytes,
    ]
    if len(challenge_hash) == 64:
        try:
            raw_hash_bytes = bytes.fromhex(challenge_hash)
            candidates.append(raw_hash_bytes)
            candidates.append(hashlib.sha256(sep53_prefix + raw_hash_bytes).digest())
        except Exception:
            pass

    for candidate in candidates:
        if not candidate:
            continue
        try:
            kp.verify(candidate, sig_bytes)
            cache.delete(cache_key)
            logger.info(f"verify_challenge: Successfully verified signature for {wallet_address}")
            return True
        except Exception:
            continue

    logger.warning(f"verify_challenge: Signature did not match any payload candidate for {wallet_address}")
    return False


# Escrow transactions

def build_fund_escrow_tx(
    poster_address: str,
    amount: str,
    asset_code: str,
    deadline_ts: int,
    memo_text: str = "",
) -> str:
    """
    Build and prepare the escrow deposit transaction.

    For Soroban paths: simulates and prepares the transaction so it includes
    the correct footprint and resource fee. The wallet (Freighter/Rabet) can
    then sign the prepared XDR directly without needing to simulate itself.

    Returns prepared (unsigned for Soroban, unsigned for classic) XDR.
    """
    server = _get_horizon_server()
    contract_id = getattr(settings, "SOROBAN_CONTRACT_ID", "")

    _ensure_account_has_balance(poster_address, Decimal(amount))

    poster_account = server.load_account(poster_address)

    builder = TransactionBuilder(
        source_account=poster_account,
        network_passphrase=_get_network_passphrase(),
        base_fee=100,
    )

    amount_int = int(Decimal(amount) * 10_000_000)

    if contract_id:
        token_address = _get_token_address(asset_code)
        parameters = [
            scval.to_address(poster_address),
            scval.to_address(token_address),
            scval.to_int128(amount_int),
            scval.to_uint64(int(deadline_ts)),
        ]
        builder.append_invoke_contract_function_op(
            contract_id=contract_id,
            function_name="create_bounty",
            parameters=parameters,
        )
        # NOTE: Soroban InvokeHostFunction transactions do NOT support memos.
        builder.set_timeout(300)
        tx = builder.build()

        # CRITICAL: Soroban txs MUST be simulated+prepared before signing.
        # This adds the Wasm footprint, auth entries, and correct resource fee.
        prepared_tx = _prepare_soroban_transaction(tx)
        return prepared_tx.to_xdr()
    else:
        escrow_kp = _get_escrow_keypair()
        asset = (
            Asset.native()
            if asset_code == "XLM"
            else Asset(settings.USDC_ASSET_CODE, settings.USDC_ASSET_ISSUER)
        )
        builder.append_payment_op(
            destination=escrow_kp.public_key,
            asset=asset,
            amount=str(amount),
        )
        if memo_text:
            builder.add_text_memo(memo_text[:28])
        builder.set_timeout(300)
        tx = builder.build()
        return tx.to_xdr()


def execute_server_escrow_funding(
    bounty_id: str,
    poster_address: str,
    amount: str,
    asset_code: str,
    deadline_ts: int,
    memo_text: str = "",
) -> dict:
    """
    Execute an on-chain escrow lock transaction directly on Stellar Testnet.

    DEMO / TESTNET-ONLY: The escrow keypair acts as the relayer. In production,
    posters must sign their own funding transactions via their wallet.
    """
    if not getattr(settings, "DEBUG", False):
        logger.warning(
            "execute_server_escrow_funding called outside DEBUG mode for bounty %s. "
            "This debits the server escrow wallet. Ensure posters sign their own transactions in production.",
            bounty_id,
        )

    server = _get_horizon_server()
    escrow_kp = _get_escrow_keypair()
    contract_id = getattr(settings, "SOROBAN_CONTRACT_ID", "")

    _ensure_account_has_balance(escrow_kp.public_key, Decimal(amount))
    _ensure_account_exists_on_testnet(poster_address)

    escrow_account = server.load_account(escrow_kp.public_key)

    builder = TransactionBuilder(
        source_account=escrow_account,
        network_passphrase=_get_network_passphrase(),
        base_fee=100,
    )

    amount_int = int(Decimal(amount) * 10_000_000)

    if contract_id:
        try:
            token_address = _get_token_address(asset_code)
            parameters = [
                scval.to_address(escrow_kp.public_key),
                scval.to_address(token_address),
                scval.to_int128(amount_int),
                scval.to_uint64(int(deadline_ts)),
            ]
            builder.append_invoke_contract_function_op(
                contract_id=contract_id,
                function_name="create_bounty",
                parameters=parameters,
            )
            # NOTE: Soroban InvokeHostFunction transactions do NOT support memos.
            builder.set_timeout(300)
            tx = builder.build()

            # Simulate+prepare BEFORE signing — Soroban requirement
            prepared_tx = _prepare_soroban_transaction(tx)
            prepared_tx.sign(escrow_kp)
            return submit_transaction(prepared_tx.to_xdr())

        except Exception as err:
            logger.warning(f"Soroban contract invocation failed ({err}), falling back to escrow payment.")
            # Reset builder and fall through to classic payment below
            escrow_account = server.load_account(escrow_kp.public_key)
            builder = TransactionBuilder(
                source_account=escrow_account,
                network_passphrase=_get_network_passphrase(),
                base_fee=100,
            )

    # Classic payment fallback
    asset = (
        Asset.native()
        if asset_code == "XLM"
        else Asset(settings.USDC_ASSET_CODE, settings.USDC_ASSET_ISSUER)
    )
    builder.append_payment_op(
        destination=escrow_kp.public_key,
        asset=asset,
        amount=str(amount),
    )
    memo = memo_text or f"fund:{str(bounty_id)[:20]}"
    builder.add_text_memo(memo[:28])
    builder.set_timeout(300)
    tx = builder.build()
    tx.sign(escrow_kp)
    return submit_transaction(tx.to_xdr())


def build_release_payment_tx(
    bounty_id: str,
    contributor_address: str,
    poster_address: str,
    amount: str,
    asset_code: str,
    memo_text: str = "",
) -> str:
    """
    Build, prepare, and sign transaction releasing funds from escrow to contributor.
    Returns signed XDR ready for submission.
    """
    server = _get_horizon_server()
    escrow_kp = _get_escrow_keypair()
    contract_id = getattr(settings, "SOROBAN_CONTRACT_ID", "")

    _ensure_account_has_balance(escrow_kp.public_key, Decimal(amount))
    _ensure_account_exists_on_testnet(contributor_address)

    escrow_account = server.load_account(escrow_kp.public_key)

    builder = TransactionBuilder(
        source_account=escrow_account,
        network_passphrase=_get_network_passphrase(),
        base_fee=100,
    )

    if contract_id and str(bounty_id).isdigit():
        try:
            parameters = [
                scval.to_uint64(int(bounty_id)),
                scval.to_address(poster_address),
            ]
            builder.append_invoke_contract_function_op(
                contract_id=contract_id,
                function_name="approve_and_pay",
                parameters=parameters,
            )
            builder.set_timeout(300)
            tx = builder.build()

            # Simulate+prepare BEFORE signing
            prepared_tx = _prepare_soroban_transaction(tx)
            prepared_tx.sign(escrow_kp)
            return prepared_tx.to_xdr()
        except Exception as err:
            logger.warning(
                f"Soroban approve_and_pay preparation failed ({err}), falling back to direct escrow payment."
            )
            escrow_account = server.load_account(escrow_kp.public_key)
            builder = TransactionBuilder(
                source_account=escrow_account,
                network_passphrase=_get_network_passphrase(),
                base_fee=100,
            )

    asset = (
        Asset.native()
        if asset_code == "XLM"
        else Asset(settings.USDC_ASSET_CODE, settings.USDC_ASSET_ISSUER)
    )
    builder.append_payment_op(
        destination=contributor_address,
        asset=asset,
        amount=str(amount),
    )
    if memo_text:
        builder.add_text_memo(memo_text[:28])
    builder.set_timeout(300)
    tx = builder.build()
    tx.sign(escrow_kp)
    return tx.to_xdr()


def build_refund_tx(
    bounty_id: str,
    poster_address: str,
    amount: str,
    asset_code: str,
    memo_text: str = "",
) -> str:
    """
    Build, prepare, and sign refund transaction returning funds from escrow to poster.
    Returns signed XDR ready for submission.
    """
    server = _get_horizon_server()
    escrow_kp = _get_escrow_keypair()
    contract_id = getattr(settings, "SOROBAN_CONTRACT_ID", "")

    _ensure_account_has_balance(escrow_kp.public_key, Decimal(amount))
    _ensure_account_exists_on_testnet(poster_address)

    escrow_account = server.load_account(escrow_kp.public_key)

    builder = TransactionBuilder(
        source_account=escrow_account,
        network_passphrase=_get_network_passphrase(),
        base_fee=100,
    )

    if contract_id and str(bounty_id).isdigit():
        try:
            parameters = [
                scval.to_uint64(int(bounty_id)),
            ]
            builder.append_invoke_contract_function_op(
                contract_id=contract_id,
                function_name="expire_and_refund",
                parameters=parameters,
            )
            builder.set_timeout(300)
            tx = builder.build()

            # Simulate+prepare BEFORE signing
            prepared_tx = _prepare_soroban_transaction(tx)
            prepared_tx.sign(escrow_kp)
            return prepared_tx.to_xdr()
        except Exception as err:
            logger.warning(
                f"Soroban expire_and_refund preparation failed ({err}), falling back to direct escrow refund."
            )
            escrow_account = server.load_account(escrow_kp.public_key)
            builder = TransactionBuilder(
                source_account=escrow_account,
                network_passphrase=_get_network_passphrase(),
                base_fee=100,
            )

    asset = (
        Asset.native()
        if asset_code == "XLM"
        else Asset(settings.USDC_ASSET_CODE, settings.USDC_ASSET_ISSUER)
    )
    builder.append_payment_op(
        destination=poster_address,
        asset=asset,
        amount=str(amount),
    )
    if memo_text:
        builder.add_text_memo(memo_text[:28])
    builder.set_timeout(300)
    tx = builder.build()
    tx.sign(escrow_kp)
    return tx.to_xdr()


def sign_and_submit_with_secret(secret_key: str, unsigned_xdr: str) -> dict:
    """
    Sign an unsigned transaction envelope with a provided Stellar secret key (S...)
    and broadcast it to the correct network (Soroban RPC or Horizon).
    """
    kp = Keypair.from_secret(secret_key)
    te = TransactionEnvelope.from_xdr(unsigned_xdr, _get_network_passphrase())
    te.sign(kp)
    return submit_transaction(te.to_xdr())


def submit_transaction(signed_xdr: str) -> dict:
    """
    Submit a signed transaction XDR to the correct network endpoint.

    Routes automatically:
    - InvokeHostFunction (Soroban) → SorobanRPC send_transaction + polling
    - Classic operations → Horizon submit_transaction

    This routing is critical: submitting a Soroban tx to Horizon always fails
    with tx_malformed (Horizon cannot execute Wasm).
    """
    if _is_soroban_transaction(signed_xdr):
        logger.info("Routing transaction to SorobanRPC (InvokeHostFunction detected)")
        return _submit_soroban_transaction(signed_xdr)

    # Classic Horizon path
    server = _get_horizon_server()
    try:
        response = server.submit_transaction(signed_xdr)
        tx_hash = response.get("hash") or response.get("id") or ""
        # Use the explicit "successful" field; a hash may be present for failed txs too
        successful = response.get("successful", False)
        return {
            "successful": successful,
            "hash": tx_hash,
            "result": response,
        }
    except BadRequestError as e:
        logger.error(f"Horizon BadRequestError: {e}")
        extras = getattr(e, "extras", {})
        return {
            "successful": False,
            "hash": "",
            "result": {"error": str(e), "extras": extras},
        }
    except Exception as e:
        logger.error(f"Horizon submit error: {e}")
        return {
            "successful": False,
            "hash": "",
            "result": {"error": str(e)},
        }


def check_transaction_status(tx_hash: str) -> dict:
    """Query Horizon for the status of a classic transaction by hash."""
    server = _get_horizon_server()
    try:
        tx = server.transactions().transaction(tx_hash).call()
        return {
            "found": True,
            "successful": tx.get("successful", False),
            "details": tx,
        }
    except NotFoundError:
        return {
            "found": False,
            "successful": False,
            "details": None,
        }


def check_soroban_transaction_status(tx_hash: str) -> dict:
    """Query SorobanRPC for the status of a Soroban transaction by hash."""
    soroban = _get_soroban_server()
    try:
        get_resp = soroban.get_transaction(tx_hash)
        return {
            "found": get_resp.status != GetTransactionStatus.NOT_FOUND,
            "successful": get_resp.status == GetTransactionStatus.SUCCESS,
            "details": get_resp,
        }
    except Exception as e:
        return {"found": False, "successful": False, "details": str(e)}


def check_account_exists(wallet_address: str) -> bool:
    """Check if a Stellar account exists on the network."""
    server = _get_horizon_server()
    try:
        server.load_account(wallet_address)
        return True
    except NotFoundError:
        return False
    except Exception:
        return False