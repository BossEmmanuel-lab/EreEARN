"""
Management command to check for and expire overdue bounties.

Triggers the Soroban escrow contract `expire_and_refund` function on-chain
to return locked funds to the poster before updating local bounty status.

Run periodically via cron or Celery beat:
    python manage.py check_expired_bounties
"""

import logging
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction
from django.utils import timezone

from api.models import Bounty, Transaction
from api.services.stellar import build_refund_tx, submit_transaction
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Check for bounties past their deadline, trigger on-chain refund to poster, and mark as EXPIRED."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report expired bounties without executing on-chain refund or changing status.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()

        expired_bounties = Bounty.objects.select_related("poster").filter(
            deadline__lt=now,
            status__in=[Bounty.Status.POSTED, Bounty.Status.CLAIMED],
        )

        count = expired_bounties.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No expired bounties found."))
            return

        for bounty in expired_bounties:
            if dry_run:
                self.stdout.write(
                    f"  [DRY RUN] Would expire: {bounty.title} "
                    f"(deadline: {bounty.deadline}, status: {bounty.status})"
                )
            else:
                # Guard: only attempt the on-chain refund if the bounty has a real
                # Soroban contract ID.  A non-digit or absent ID would silently fall
                # through to the plain payment fallback in build_refund_tx, which may
                # operate on an unconfigured escrow account.
                if not bounty.escrow_bounty_id or not bounty.escrow_bounty_id.isdigit():
                    logger.warning(
                        "Bounty %s has no valid on-chain ID (escrow_bounty_id=%r), "
                        "skipping on-chain refund and marking expired locally.",
                        bounty.id,
                        bounty.escrow_bounty_id,
                    )
                    bounty.status = Bounty.Status.EXPIRED
                    bounty.save(update_fields=["status", "updated_at"])
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Expired (no valid on-chain ID, skipped refund): {bounty.title}"
                        )
                    )
                    continue

                # Resolve the correct escrow address (contract or hot-wallet) for
                # the transaction record.
                escrow_address = (
                    getattr(settings, "SOROBAN_CONTRACT_ID", "")
                    or getattr(settings, "STELLAR_ESCROW_PUBLIC", "")
                )

                try:
                    # Trigger on-chain refund transaction
                    signed_xdr = build_refund_tx(
                        bounty_id=bounty.escrow_bounty_id,
                        poster_address=bounty.poster.wallet_address,
                        amount=str(bounty.reward_amount),
                        asset_code=bounty.reward_asset,
                        memo_text=f"refund:{str(bounty.id)[:20]}",
                    )
                    result = submit_transaction(signed_xdr)

                    with db_transaction.atomic():
                        bounty.status = Bounty.Status.EXPIRED
                        bounty.save(update_fields=["status", "updated_at"])

                        tx_status = (
                            Transaction.Status.SUCCESS
                            if result["successful"]
                            else Transaction.Status.FAILED
                        )
                        Transaction.objects.create(
                            bounty=bounty,
                            tx_hash=result.get("hash") or None,
                            tx_type=Transaction.TxType.REFUND,
                            from_address=escrow_address,
                            to_address=bounty.poster.wallet_address,
                            amount=bounty.reward_amount,
                            asset=bounty.reward_asset,
                            status=tx_status,
                        )

                    if result["successful"]:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"  Expired & Refunded: {bounty.title} (TX: {result.get('hash')})"
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  Expired (refund failed on-chain): {bounty.title}"
                            )
                        )
                except Exception as e:
                    logger.error(f"Failed on-chain refund for bounty {bounty.id}: {e}")
                    # Do NOT silently flip to EXPIRED on a refund error — the funds
                    # may still be locked in escrow.  Log and leave for manual review.
                    self.stdout.write(
                        self.style.ERROR(
                            f"  Refund error (bounty NOT marked expired): {bounty.title} — {e}"
                        )
                    )

        action = "would be expired" if dry_run else "expired and processed"
        self.stdout.write(
            self.style.SUCCESS(f"\n{count} bounty(ies) {action}.")
        )
