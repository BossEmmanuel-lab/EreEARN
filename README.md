# ÉréEARN API

> **Stellar and Soroban-powered Bounty Marketplace Backend**  
> Headless Django REST API with trustless on-chain escrow smart contracts deployed on Stellar Testnet.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Repository Structure](#3-repository-structure)
4. [Quick Start with Docker](#4-quick-start-with-docker)
5. [Local Development Setup](#5-local-development-setup)
6. [Environment Variables](#6-environment-variables)
7. [API Reference & Swagger Documentation](#7-api-reference--swagger-documentation)
8. [Backend Implementation Reference](#8-backend-implementation-reference)
   - [Models (`api/models.py`)](#81-models)
   - [Serializers (`api/serializers.py`)](#82-serializers)
   - [Views (`api/views.py`)](#83-views)
   - [Stellar Service (`api/services/stellar.py`)](#84-stellar-service)
   - [Management Commands (`api/management/commands/`)](#85-management-commands)
9. [Soroban Smart Contract Reference](#9-soroban-smart-contract-reference)
   - [Contract Constants & Storage](#91-constants--storage)
   - [Data Types & Errors](#92-data-types--errors)
   - [Admin Functions](#93-admin-functions)
   - [Bounty Lifecycle Functions](#94-bounty-lifecycle-functions)
   - [Approval & Settlement Functions](#95-approval--settlement-functions)
   - [Expiry & Dispute Functions](#96-expiry--dispute-functions)
   - [Query Functions](#97-query-functions)
10. [Docker Configuration](#10-docker-configuration)
11. [Testing & Verification](#11-testing--verification)

---

## 1. Overview

ÉréEARN provides a decentralized escrow backend for bounty-based work:

- **Posters** fund bounties with XLM or USDC locked directly into a Soroban smart contract.
- **Contributors** authenticate via their Stellar wallet (no passwords), claim bounties, and submit proof of work.
- **Review & Settlement**: Posters review submissions. When approved, funds are automatically released on-chain to the contributor.
- **Expiry & Auto-refund**: If a bounty passes its deadline without a submission, escrowed funds can be refunded to the poster on-chain.
- **Dispute Resolution**: Contract admin can split funds proportionally between poster and contributor in case of irreconcilable disputes.

The backend is completely **headless** and optimized for integration with modern frontend frameworks such as React or Next.js.

---

## 2. Architecture

```
                      ┌────────────────────────────────────────┐
                      │    React / Next.js Client Application  │
                      │       (Freighter / Rabet Wallet)       │
                      └───────────────────┬────────────────────┘
                                          │ HTTPS / REST / JWT
                                          ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               ÉréEARN Headless API Server                              │
│                                                                                        │
│   ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────────┐   │
│   │ Authentication      │  │ Bounty & Submission │  │ Dashboard & Audit            │   │
│   │ /api/v1/auth/*      │  │ /api/v1/bounties/*  │  │ /api/v1/dashboard/*          │   │
│   └──────────┬──────────┘  └──────────┬──────────┘  │ /api/v1/transactions/*       │   │
│              │                        │             └──────────────┬───────────────┘   │
│              └────────────────────────┼────────────────────────────┘                   │
│                                       ▼                                                │
│                        ┌──────────────────────────────┐                                │
│                        │   api/services/stellar.py    │                                │
│                        │   (Simulation, XDR & RPC)    │                                │
│                        └──────────────┬───────────────┘                                │
│                                       │                                                │
│                 ┌─────────────────────┴─────────────────────┐                          │
│                 ▼                                           ▼                          │
│     ┌───────────────────────┐                   ┌───────────────────────┐              │
│     │ Database (SQLite/PG)  │                   │ Cache (Redis/Memory)  │              │
│     └───────────────────────┘                   └───────────────────────┘              │
└───────────────────────────────────────┬────────────────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               Stellar Network (Testnet)                                │
│                                                                                        │
│         Horizon REST API                      Soroban JSON-RPC                         │
│   (Account balances, fallback)      (Contract simulation, footprint, prep)             │
│                 │                                           │                          │
│                 └─────────────────────┬─────────────────────┘                          │
│                                       ▼                                                │
│                    ┌──────────────────────────────────────┐                            │
│                    │    EscrowContract (Soroban Wasm)     │                            │
│                    │    - create_bounty                   │                            │
│                    │    - claim_bounty                    │                            │
│                    │    - submit_work / reject_work       │                            │
│                    │    - approve_and_pay                 │                            │
│                    │    - expire_and_refund               │                            │
│                    │    - resolve_dispute                 │                            │
│                    └──────────────────────────────────────┘                            │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Repository Structure

```
ÉréEARN/
├── Core/
│   ├── settings.py           # Application settings and environment configuration
│   ├── urls.py               # Root router (redirects "/" to Swagger UI)
│   ├── wsgi.py               # WSGI production server entry point
│   └── asgi.py               # ASGI async server entry point
├── api/
│   ├── models.py             # User, Bounty, Submission, Transaction
│   ├── serializers.py        # Django REST Framework serializers and validation
│   ├── views.py              # API view endpoints and business logic
│   ├── urls.py               # API route definitions under /api/v1/
│   ├── tests.py              # Comprehensive test suite (20 automated tests)
│   ├── admin.py              # Django administration portal registrations
│   ├── apps.py               # Application configuration
│   ├── services/
│   │   └── stellar.py        # Stellar SDK and Soroban RPC client integration
│   ├── management/
│   │   └── commands/
│   │       └── check_expired_bounties.py  # Background refund and expiry command
│   └── migrations/           # Database migration files
├── contracts/
│   └── escrow/
│       ├── Cargo.toml        # Soroban Rust crate manifest
│       └── src/
│           └── lib.rs        # Soroban escrow contract source code
├── Dockerfile                # Multi-stage container build
├── docker-compose.yml        # Orchestration (db, redis, web, worker, beat)
├── .dockerignore             # Docker build context exclusion list
├── .gitignore                # Git repository exclusion list
├── .env.example              # Sample environment variables
├── requirements.txt          # Python dependencies
└── manage.py                 # Django management script
```

---

## 4. Quick Start with Docker

### Prerequisites

- Docker Engine ≥ 24
- Docker Compose v2

### Steps

1. Clone and enter the repository:

   ```bash
   git clone https://github.com/your-org/ÉréEARN.git
   cd ÉréEARN
   ```

2. Copy the sample environment file:

   ```bash
   cp .env.example .env
   ```

3. Launch all services:

   ```bash
   docker compose up --build -d
   ```

4. Run migrations:

   ```bash
   docker compose exec web python manage.py migrate
   ```

5. Access the API documentation:
   - **Swagger UI**: [http://localhost:8000/api/v1/docs/](http://localhost:8000/api/v1/docs/)
   - **ReDoc**: [http://localhost:8000/api/v1/redoc/](http://localhost:8000/api/v1/redoc/)
   - **OpenAPI Schema**: [http://localhost:8000/api/v1/schema/](http://localhost:8000/api/v1/schema/)

---

## 5. Local Development Setup

### Prerequisites

- Python 3.12+
- Redis (optional for development, in-memory cache used as fallback)
- Rust and `cargo` with `wasm32-unknown-unknown` target (for contract builds)

### Installation

1. Create and activate a Python virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install Python packages:

   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. Set up the environment file:

   ```bash
   cp .env.example .env
   ```

4. Apply database migrations:

   ```bash
   python manage.py migrate
   ```

5. Run the development server:

   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```

6. Open [http://127.0.0.1:8000/api/v1/docs/](http://127.0.0.1:8000/api/v1/docs/) in your browser.

---

## 6. Environment Variables

| Variable                     | Required   | Default                               | Description                                                 |
| ---------------------------- | ---------- | ------------------------------------- | ----------------------------------------------------------- |
| `SECRET_KEY`                 | Yes        | Insecure placeholder                  | Django secret key for session and cryptographic signing     |
| `DEBUG`                      | No         | `True`                                | Debug flag (set `False` in production)                      |
| `ALLOWED_HOSTS`              | No         | `localhost,127.0.0.1`                 | Comma-delimited list of valid host headers                  |
| `CORS_ALLOWED_ORIGINS`       | No         | `http://localhost:3000`               | Allowed origins for React frontend CORS requests            |
| `CORS_ALLOW_ALL_ORIGINS`     | No         | `False`                               | When True, allows all CORS origins (useful for initial dev) |
| `STELLAR_NETWORK`            | No         | `TESTNET`                             | Stellar network mode (`TESTNET` or `MAINNET`)               |
| `STELLAR_HORIZON_URL`        | No         | `https://horizon-testnet.stellar.org` | Horizon API endpoint                                        |
| `STELLAR_NETWORK_PASSPHRASE` | No         | `Test SDF Network ; September 2015`   | Network passphrase for transaction signing                  |
| `SOROBAN_RPC_URL`            | No         | `https://soroban-testnet.stellar.org` | Soroban JSON-RPC endpoint                                   |
| `SOROBAN_CONTRACT_ID`        | No         | `CDLQ...`                             | Address of deployed Soroban escrow contract                 |
| `SOROBAN_USDC_CONTRACT_ID`   | No         | `CBIELTK...`                          | Soroban SAC contract ID for testnet USDC                    |
| `USDC_ASSET_ISSUER`          | No         | `GBBD47...`                           | Testnet USDC issuing account address                        |
| `STELLAR_ESCROW_SECRET`      | Yes (prod) | Auto-generated in dev                 | Secret key (`S...`) of the escrow funding hot wallet        |
| `STELLAR_ESCROW_PUBLIC`      | No         | Auto-derived from secret              | Public key (`G...`) of the escrow funding hot wallet        |
| `REDIS_URL`                  | No         | Empty                                 | Redis server URL (e.g. `redis://localhost:6379/0`)          |
| `CELERY_BROKER_URL`          | No         | Empty                                 | Celery broker URL                                           |

---

## 7. API Reference & Swagger Documentation

Interactive OpenAPI 3.1 documentation is served directly from the application:

- **Swagger UI**: `/api/v1/docs/`
- **ReDoc**: `/api/v1/redoc/`
- **Raw Schema**: `/api/v1/schema/`
- **Root**: Navigating to `/` redirects directly to `/api/v1/docs/`.

### Endpoints Table

| Tag                | Method  | Path                                     | Auth | Description                                    |
| ------------------ | ------- | ---------------------------------------- | ---- | ---------------------------------------------- |
| **Authentication** | `POST`  | `/api/v1/auth/challenge/`                | None | Generate wallet challenge nonce                |
| **Authentication** | `POST`  | `/api/v1/auth/verify/`                   | None | Verify Ed25519 signature and obtain JWT        |
| **Authentication** | `POST`  | `/api/v1/auth/refresh/`                  | None | Refresh expired JWT access token               |
| **Authentication** | `GET`   | `/api/v1/auth/me/`                       | JWT  | Get current user profile                       |
| **Authentication** | `PATCH` | `/api/v1/auth/me/`                       | JWT  | Update user bio, avatar URL, or role           |
| **Bounties**       | `GET`   | `/api/v1/bounties/`                      | None | List bounties with filters and search          |
| **Bounties**       | `POST`  | `/api/v1/bounties/prepare-fund/`         | JWT  | Build unsigned funding transaction XDR         |
| **Bounties**       | `POST`  | `/api/v1/bounties/create/`               | JWT  | Create bounty and broadcast signed funding XDR |
| **Bounties**       | `GET`   | `/api/v1/bounties/<uuid:pk>/`            | None | Retrieve bounty details, submissions & txs     |
| **Bounties**       | `POST`  | `/api/v1/bounties/<uuid:pk>/claim/`      | JWT  | Claim open bounty (contributors only)          |
| **Bounties**       | `POST`  | `/api/v1/bounties/<uuid:pk>/expire/`     | JWT  | Refund overdue bounty to poster on-chain       |
| **Submissions**    | `POST`  | `/api/v1/bounties/<uuid:pk>/submit/`     | JWT  | Submit deliverable for claimed bounty          |
| **Submissions**    | `POST`  | `/api/v1/bounties/<uuid:pk>/review/`     | JWT  | Approve (and pay on-chain) or request revision |
| **Dashboards**     | `GET`   | `/api/v1/dashboard/contributor/`         | JWT  | Contributor active claims and earnings         |
| **Dashboards**     | `GET`   | `/api/v1/dashboard/poster/`              | JWT  | Poster bounties, reviews, and spend            |
| **Transactions**   | `GET`   | `/api/v1/transactions/<uuid:bounty_id>/` | JWT  | List on-chain transactions for a bounty        |

---

## 8. Backend Implementation Reference

### 8.1 Models

#### `User` (`api/models.py`)

Custom user model using UUID primary keys and Stellar wallet public keys for identification.

- `id`: `UUIDField` primary key.
- `wallet_address`: `CharField(max_length=56, unique=True, db_index=True)` Stellar public key (starts with `G`).
- `role`: `CharField(choices=["POSTER", "CONTRIBUTOR"])`. Default is `CONTRIBUTOR`.
- `bio`: `TextField` for profile summary.
- `avatar_url`: `URLField` for profile image.
- `updated_at`: `DateTimeField(auto_now=True)`.

#### `Bounty` (`api/models.py`)

Central bounty entity.

- `id`: `UUIDField` primary key.
- `poster`: `ForeignKey(User, related_name="posted_bounties")`.
- `title`: `CharField(max_length=255)`.
- `description`: `TextField`.
- `skill_category`: Choice field (`DEVELOPMENT`, `DESIGN`, `WRITING`, `VIDEO`, `PROJECT_MANAGEMENT`, `COMMUNITY`).
- `reward_amount`: `DecimalField(max_digits=20, decimal_places=7)`.
- `reward_asset`: Choice field (`XLM`, `USDC`).
- `deadline`: `DateTimeField` (minimum 24 hours into the future).
- `status`: Choice field (`POSTED`, `CLAIMED`, `SUBMITTED`, `APPROVED`, `PAID`, `EXPIRED`).
- `escrow_tx_hash`: `CharField(max_length=64)` transaction hash of funding transaction.
- `escrow_bounty_id`: `CharField(max_length=128)` ID returned by the Soroban contract.
- `payment_tx_hash`: `CharField(max_length=64)` transaction hash of payment release.
- `contributor`: `ForeignKey(User, null=True, blank=True, related_name="claimed_bounties")`.
- `is_expired`: Boolean property returning `True` when current time exceeds `deadline` and status is still `POSTED` or `CLAIMED`.

#### `Submission` (`api/models.py`)

Deliverables submitted by contributors.

- `id`: `UUIDField` primary key.
- `bounty`: `ForeignKey(Bounty, related_name="submissions")`.
- `contributor`: `ForeignKey(User, related_name="submissions")`.
- `submission_text`: `TextField` describing deliverable.
- `submission_url`: `URLField` linking to PR, repo, document, or design asset.
- `reviewer_notes`: `TextField` containing poster feedback.
- `status`: Choice field (`PENDING`, `APPROVED`, `REVISION_REQUESTED`).

#### `Transaction` (`api/models.py`)

On-chain transaction audit trail.

- `id`: `UUIDField` primary key.
- `bounty`: `ForeignKey(Bounty, related_name="transactions")`.
- `tx_hash`: `CharField(max_length=64, unique=True, db_index=True)`.
- `tx_type`: Choice field (`FUND_ESCROW`, `RELEASE_PAYMENT`, `REFUND`).
- `from_address`: `CharField(max_length=56)` sender public key or contract address.
- `to_address`: `CharField(max_length=56)` destination public key or contract address.
- `amount`: `DecimalField(max_digits=20, decimal_places=7)`.
- `asset`: `CharField(max_length=4)`.
- `status`: Choice field (`PENDING`, `SUCCESS`, `FAILED`).

---

### 8.2 Serializers

#### `WalletChallengeSerializer` (`api/serializers.py`)

- Validates that `wallet_address` begins with `G` and is exactly 56 characters long.

#### `WalletVerifySerializer` (`api/serializers.py`)

- Accepts `wallet_address`, hex-encoded Ed25519 `signature`, and optional initial `role`.

#### `BountyCreateSerializer` (`api/serializers.py`)

- Validates `deadline` is at least 24 hours in the future (`MIN_DEADLINE_DURATION`).
- Validates `reward_amount` is strictly greater than zero.
- Accepts optional `signed_xdr` for wallet funding.

#### `SubmissionCreateSerializer` (`api/serializers.py`)

- Requires that either `submission_text` or `submission_url` is provided.

#### `SubmissionReviewSerializer` (`api/serializers.py`)

- Validates action (`approve` or `request_revision`).
- Enforces that `reviewer_notes` are provided when `action="request_revision"`.

#### `TransactionSerializer` (`api/serializers.py`)

- Adds computed `explorer_url` linking to Stellar Expert block explorer.

---

### 8.3 Views

#### `WalletChallengeView`

- **Method**: `POST`
- **Auth**: None
- Generates a 32-byte cryptographic random nonce cached with key `auth_challenge:<wallet_address>`.
- Returns challenge message, hash, and expiration timestamp.

#### `WalletVerifyView`

- **Method**: `POST`
- **Auth**: None
- Verifies Ed25519 signature against challenge message.
- Retrieves or creates `User`, issues JWT `access` and `refresh` tokens.

#### `UserProfileView`

- **Method**: `GET`, `PATCH`
- **Auth**: JWT required
- Retrieves or updates user `bio`, `avatar_url`, and `role`.

#### `BountyListView`

- **Method**: `GET`
- **Auth**: None
- Supports filtering by `skill_category`, `status`, and `reward_asset`.
- Full-text search across `title` and `description`.
- Ordering by `created_at`, `reward_amount`, and `deadline`.

#### `BountyPrepareFundView`

- **Method**: `POST`
- **Auth**: JWT required
- Calls `build_fund_escrow_tx` with poster's wallet address.
- Returns unsigned base64 XDR ready for Freighter/Rabet client-side signing.

#### `BountyCreateView`

- **Method**: `POST`
- **Auth**: JWT required
- Saves bounty record in database.
- If `signed_xdr` is provided, broadcasts client transaction via `submit_transaction`.
- In testnet debug mode, falls back to server-side escrow funding via `execute_server_escrow_funding`.
- Logs transaction audit entry.

#### `BountyDetailView`

- **Method**: `GET`
- **Auth**: None
- Returns complete bounty record including nested submissions and transaction logs.

#### `BountyClaimView`

- **Method**: `POST`
- **Auth**: JWT required (Contributor role only)
- Uses `select_for_update()` inside a database transaction to prevent concurrent race conditions.
- Rejects self-claims by posters and claims on expired or claimed bounties.
- Sets bounty status to `CLAIMED` and assigns contributor.

#### `SubmissionCreateView`

- **Method**: `POST`
- **Auth**: JWT required (Assigned contributor only)
- Creates `Submission` record and advances bounty status to `SUBMITTED`.

#### `SubmissionReviewView`

- **Method**: `POST`
- **Auth**: JWT required (Bounty poster only)
- If `action="request_revision"`, sets submission to `REVISION_REQUESTED` and returns bounty to `CLAIMED`.
- If `action="approve"`, executes `build_release_payment_tx` and `submit_transaction` on Stellar before updating database status to `APPROVED` and `PAID`.

#### `BountyExpireView`

- **Method**: `POST`
- **Auth**: JWT required (Poster or staff)
- Verifies deadline has passed and status is `POSTED` or `CLAIMED`.
- Executes `build_refund_tx` to return tokens on-chain and updates bounty status to `EXPIRED`.

#### `ContributorDashboardView`

- **Method**: `GET`
- **Auth**: JWT required
- Returns active claims, pending submissions, completed bounties, and total earned.

#### `PosterDashboardView`

- **Method**: `GET`
- **Auth**: JWT required
- Returns active bounties, claimed bounties, pending reviews, completed bounties, total posted, and total spent.

#### `TransactionListView`

- **Method**: `GET`
- **Auth**: JWT required (Participants only: poster or contributor)
- Returns on-chain transaction history for a specific bounty.

---

### 8.4 Stellar Service (`api/services/stellar.py`)

#### Configuration & Connection Functions

- `_get_horizon_server() -> Server`: Returns configured Horizon client.
- `_get_soroban_server() -> SorobanServer`: Returns configured Soroban RPC client.
- `_get_network_passphrase() -> str`: Returns active Stellar network passphrase.
- `_is_testnet() -> bool`: Returns `True` if active network is Testnet.
- `_is_soroban_transaction(xdr: str) -> bool`: Checks if transaction envelope contains `InvokeHostFunction` operations.

#### Account & Token Helpers

- `_ensure_account_has_balance(public_key, min_xlm)`: Verifies account balance on Testnet; calls Friendbot if balance is insufficient.
- `_ensure_account_exists_on_testnet(public_key)`: Checks account existence on Testnet and creates via Friendbot if missing.
- `_get_escrow_keypair() -> Keypair`: Safely caches and returns server escrow signing keypair.
- `_get_token_contract_id(asset_code: str) -> str`: Resolves Soroban contract address for native XLM (SAC derived) or USDC (`SOROBAN_USDC_CONTRACT_ID` or issuer-derived SAC).
- `_get_escrow_address() -> str`: Returns Soroban contract address or fallback server public key.

#### Transaction Simulation & Preparation

- `_prepare_soroban_transaction(tx: TransactionEnvelope) -> TransactionEnvelope`: Simulates transaction via `SorobanServer.simulate_transaction()`, retrieves Wasm ledger footprint and auth entries, calculates resource fee, and returns prepared envelope ready for signature.
- `check_soroban_transaction_status(tx_hash: str) -> dict`: Polls `SorobanServer.get_transaction()` every 3 seconds up to 30 seconds until finality (`SUCCESS` or `FAILED`).

#### Wallet Authentication Helpers

- `generate_challenge(wallet_address: str) -> dict`: Creates cryptographic challenge nonce, caches with 5-minute TTL, and returns hash and message.
- `verify_challenge(wallet_address: str, signature: str) -> bool`: Verifies Ed25519 signature of challenge message with public key.

#### Escrow Transaction Builders

- `build_fund_escrow_tx(poster_address, amount, asset_code, deadline_ts, memo_text) -> str`: Builds unsigned XDR calling contract function `create_bounty`.
- `execute_server_escrow_funding(bounty_id, poster_address, amount, asset_code, deadline_ts) -> dict`: Server-side funding relayer for demo/testnet use. Builds, simulates, signs, broadcasts, and polls for confirmation.
- `build_release_payment_tx(bounty_id, contributor_address, amount, asset_code, memo_text) -> str`: Builds and signs transaction calling contract function `approve_and_pay`.
- `build_refund_tx(bounty_id, poster_address, amount, asset_code, memo_text) -> str`: Builds and signs transaction calling contract function `expire_and_refund`.
- `submit_transaction(signed_xdr: str) -> dict`: Inspects transaction; routes Soroban `InvokeHostFunction` calls to Soroban RPC (`send_transaction`) with status polling; routes classic payments to Horizon (`submit_transaction`).
- `get_onchain_bounty_count() -> int`: Queries total bounties directly from Soroban contract storage via RPC simulation.

---

### 8.5 Management Commands

#### `python manage.py check_expired_bounties` (`api/management/commands/check_expired_bounties.py`)

- Scans database for bounties where `deadline < now` and status is `POSTED` or `CLAIMED`.
- Calls `build_refund_tx` and `submit_transaction` to return funds from the contract to the poster.
- Updates database status to `EXPIRED`.
- Supports `--dry-run` flag to preview expired bounties without executing transactions.

---

## 9. Soroban Smart Contract Reference

**File:** [`contracts/escrow/src/lib.rs`](contracts/escrow/src/lib.rs)

### 9.1 Constants & Storage

- `DAY_IN_LEDGERS = 17_280`: Ledgers per 24 hours (~5 seconds per ledger).
- `INSTANCE_BUMP_AMOUNT = 30 * DAY_IN_LEDGERS`: Storage TTL bump for contract instance.
- `BOUNTY_BUMP_AMOUNT = 60 * DAY_IN_LEDGERS`: Storage TTL bump for persistent bounty records.
- `MIN_DEADLINE_DURATION = 86_400`: Minimum bounty duration (24 hours in seconds).
- `REVIEW_PERIOD = 7 * 86_400`: Poster review window (7 days).
- `REVISION_PERIOD = 3 * 86_400`: Contributor revision grace period (3 days).

Storage layout:

- `DataKey::Admin`: Instance storage key for contract administrator address.
- `DataKey::PendingAdmin`: Instance storage key for two-step admin transfer.
- `DataKey::BountyCounter`: Instance storage key tracking total bounties created (`u64`).
- `DataKey::Bounty(u64)`: Persistent storage key holding `BountyEscrow` struct.

### 9.2 Data Types & Errors

#### `BountyStatus` Enum

- `Posted`: Bounty created and funded in escrow.
- `Claimed`: Claimed by a contributor.
- `Submitted`: Work submitted; review period active.
- `Approved`: Work approved by poster.
- `Paid`: Escrowed reward transferred to contributor.
- `Expired`: Deadline elapsed without submission; funds returned to poster.
- `Resolved`: Admin resolved dispute with token split.

#### `BountyEscrow` Struct

```rust
pub struct BountyEscrow {
    pub bounty_id: u64,
    pub poster: Address,
    pub contributor: Option<Address>,
    pub token: Address,
    pub amount: i128,
    pub status: BountyStatus,
    pub deadline: u64,
    pub review_deadline: Option<u64>,
    pub created_at: u64,
}
```

#### `EscrowError` Enum

- `NotFound = 1`: Bounty ID does not exist in storage.
- `InvalidStatus = 2`: Action not allowed in current status.
- `Unauthorized = 3`: Caller lacks required authorization.
- `NotExpired = 4`: Attempted refund before deadline passed.
- `InvalidAmount = 5`: Amount is zero, negative, or invalid for split.
- `InvalidDeadline = 6`: Deadline is less than 24 hours from current time.
- `SelfClaim = 7`: Poster attempted to claim their own bounty.
- `DeadlinePassed = 8`: Attempted claim or submission after deadline.
- `ReviewPeriodActive = 9`: Contributor attempted autopay claim while review period is still active.
- `ReviewPeriodExpired = 10`: Poster attempted rejection after review period elapsed.
- `InvalidToken = 11`: Token address matches contract address.

---

### 9.3 Admin Functions

#### `initialize(env: Env, admin: Address)`

- Sets contract administrator and initializes bounty counter to 0.
- Bumps instance storage TTL.
- Panics if already initialized.

#### `transfer_admin(env: Env, new_admin: Address) -> Result<(), EscrowError>`

- Requires current admin authorization (`admin.require_auth()`).
- Sets `PendingAdmin` key.
- Emits `(admin, transfer)` event.

#### `accept_admin(env: Env) -> Result<(), EscrowError>`

- Requires pending admin authorization (`pending.require_auth()`).
- Promotes pending admin to current admin; removes `PendingAdmin`.
- Emits `(admin, accepted)` event.

---

### 9.4 Bounty Lifecycle Functions

#### `create_bounty(env: Env, poster: Address, token: Address, amount: i128, deadline: u64) -> Result<u64, EscrowError>`

- Requires poster authorization (`poster.require_auth()`).
- Validates `amount > 0`, `token != contract_address`, and `deadline >= now + 86400`.
- Transfers tokens from poster to contract address.
- Increments `BountyCounter` and saves `BountyEscrow` in persistent storage.
- Bumps persistent and instance storage TTLs.
- Emits `(bounty, created)` event. Returns new `bounty_id`.

#### `claim_bounty(env: Env, bounty_id: u64, contributor: Address) -> Result<(), EscrowError>`

- Requires contributor authorization (`contributor.require_auth()`).
- Verifies bounty status is `Posted`.
- Rejects self-claims (`poster == contributor`).
- Rejects claims past the deadline.
- Assigns `contributor` and updates status to `Claimed`.
- Emits `(bounty, claimed)` event.

#### `submit_work(env: Env, bounty_id: u64, contributor: Address) -> Result<(), EscrowError>`

- Requires contributor authorization.
- Verifies status is `Claimed` and caller is assigned contributor.
- Verifies deadline has not passed.
- Updates status to `Submitted` and sets `review_deadline = now + REVIEW_PERIOD` (7 days).
- Emits `(bounty, submitted)` event.

#### `reject_work(env: Env, bounty_id: u64, poster: Address) -> Result<(), EscrowError>`

- Requires poster authorization.
- Verifies status is `Submitted` and caller is poster.
- Verifies review deadline has not elapsed.
- Resets status to `Claimed` and clears `review_deadline`.
- If original deadline has passed, extends deadline by `REVISION_PERIOD` (3 days).
- Emits `(bounty, rejected)` event.

---

### 9.5 Approval & Settlement Functions

#### `approve_work(env: Env, bounty_id: u64, poster: Address) -> Result<(), EscrowError>`

- Requires poster authorization.
- Verifies status is `Submitted` and caller is poster.
- Updates status to `Approved` and clears `review_deadline`.
- Emits `(bounty, approved)` event.

#### `claim_payment(env: Env, bounty_id: u64) -> Result<(), EscrowError>`

- Can be called by anyone once approved, or by contributor via autopay.
- If status is `Approved`: transfers tokens to contributor and marks `Paid`.
- If status is `Submitted`: verifies `current_time > review_deadline` (autopay), transfers tokens to contributor, marks `Paid`, and emits `(bounty, autopaid)`.

#### `approve_and_pay(env: Env, bounty_id: u64, poster: Address) -> Result<(), EscrowError>`

- Combined atomic approval and payment.
- Requires poster authorization.
- Verifies status is `Submitted` and caller is poster.
- Transfers tokens directly from contract to contributor.
- Updates status to `Paid` and emits `(bounty, approved)` and `(bounty, paid)` events.

---

### 9.6 Expiry & Dispute Functions

#### `expire_and_refund(env: Env, bounty_id: u64) -> Result<(), EscrowError>`

- Verifies status is `Posted` or `Claimed` (cannot expire while `Submitted` or `Approved`).
- Verifies `current_time >= deadline`.
- Transfers full token amount back to poster.
- Updates status to `Expired` and emits `(bounty, expired)` event.

#### `resolve_dispute(env: Env, bounty_id: u64, poster_amount: i128, contributor_amount: i128) -> Result<(), EscrowError>`

- Requires contract admin authorization (`admin.require_auth()`).
- Verifies status is `Submitted`.
- Verifies `poster_amount >= 0`, `contributor_amount >= 0`, and `poster_amount + contributor_amount == bounty.amount`.
- Transfers `poster_amount` to poster and `contributor_amount` to contributor.
- Updates status to `Resolved` and emits `(bounty, resolved)` event.

---

### 9.7 Query Functions

#### `get_bounty(env: Env, bounty_id: u64) -> Result<BountyEscrow, EscrowError>`

- Returns complete `BountyEscrow` struct from persistent storage.
- Extends storage TTL.

#### `get_bounty_count(env: Env) -> u64`

- Returns total number of bounties created in contract history.

#### `get_admin(env: Env) -> Address`

- Returns current administrator address.

#### `get_pending_admin(env: Env) -> Option<Address>`

- Returns pending administrator address during transfer.

---

## 10. Docker Configuration

### Multi-Stage Dockerfile (`Dockerfile`)

- **Stage 1 (builder)**: Installs build-time C-compilers and dependencies into `/install`.
- **Stage 2 (runtime)**: Copies installed Python packages into `python:3.12-slim` base image, creates non-root user `appuser`, collects static files, exposes port `8000`, and executes Gunicorn.

### Service Orchestration (`docker-compose.yml`)

- **`web`**: Django Gunicorn API server running on port `8000`.
- **`redis`**: Redis 7 cache and Celery message broker on port `6379`.
- **`worker`**: Celery worker for asynchronous background jobs.
- **`beat`**: Periodic runner executing `check_expired_bounties` every hour.

---

## 11. Testing & Verification

### Running the Test Suite

Execute the 20 automated unit and integration tests:

```bash
python manage.py test api
```

The test suite covers:

- Wallet challenge creation and nonce caching.
- Ed25519 signature verification and JWT token issuance.
- Bounty creation, validation, and listing filters.
- Unsigned XDR funding preparation and Soroban simulation.
- Contributor claim permissions and self-claim prevention.
- Deliverable submissions and revision requests.
- On-chain payment settlement and transaction audit logging.
- Deadline expiration rules and access control checks.

### Validating OpenAPI / Swagger Schema

```bash
python manage.py spectacular --validate
```

### Checking Django System Health

```bash
python manage.py check
```
