"""
API views for ÉréEARN.

Implements wallet-based authentication, bounty CRUD, on-chain testnet escrow
funding, claim/submit/review workflows, dashboards, and transaction logging.
"""

import logging
import time
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import Sum
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, OpenApiResponse
from rest_framework import filters, generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Bounty, Submission, Transaction, User
from .serializers import (
    BountyCreateSerializer,
    BountyDetailSerializer,
    BountyListSerializer,
    ContributorDashboardSerializer,
    PosterDashboardSerializer,
    ReviewAction,
    SubmissionCreateSerializer,
    SubmissionReviewSerializer,
    SubmissionSerializer,
    TransactionSerializer,
    UserProfileSerializer,
    WalletChallengeSerializer,
    WalletVerifySerializer,
)
from .services.stellar import (
    _ensure_account_exists_on_testnet,
    _get_escrow_address,
    _get_escrow_keypair,
    _is_testnet,
    build_fund_escrow_tx,
    build_refund_tx,
    build_release_payment_tx,
    check_soroban_transaction_status,
    execute_server_escrow_funding,
    generate_challenge,
    get_onchain_bounty_count,
    submit_transaction,
    verify_challenge,
)

logger = logging.getLogger(__name__)


# Authentication views


class WalletChallengeView(APIView):
    """Request a signing challenge for wallet-based authentication."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Request wallet auth challenge",
        request=WalletChallengeSerializer,
        responses={200: OpenApiResponse(description="Nonce challenge message and hash")},
        tags=["Authentication"],
    )
    def post(self, request):
        serializer = WalletChallengeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        wallet_address = serializer.validated_data["wallet_address"]
        challenge_data = generate_challenge(wallet_address)

        return Response(challenge_data, status=status.HTTP_200_OK)


class WalletVerifyView(APIView):
    """Verify signed challenge and issue JWT tokens."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Verify signed challenge and issue JWT",
        request=WalletVerifySerializer,
        responses={200: OpenApiResponse(description="JWT tokens & user profile")},
        tags=["Authentication"],
    )
    def post(self, request):
        serializer = WalletVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        wallet_address = serializer.validated_data["wallet_address"]
        signature = serializer.validated_data["signature"]
        role = serializer.validated_data.get("role", User.Role.CONTRIBUTOR)

        is_verified = verify_challenge(wallet_address, signature)

        if not is_verified:
            # Allow fallback on testnet / DEBUG for wallets lacking arbitrary message signing (Albedo, WalletConnect, Rabet)
            is_mock_or_testnet = (
                signature == "00" * 64
                or signature.startswith("0000")
                or signature in ("mock", "stellar-wallets-kit", "swk")
                or _is_testnet()
            )
            if settings.DEBUG and is_mock_or_testnet:
                logger.info(f"Allowing debug/testnet auth fallback for {wallet_address}")
            else:
                return Response(
                    {"detail": "Invalid or expired signature."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

        user, created = User.objects.get_or_create(
            wallet_address=wallet_address,
            defaults={
                "username": f"user_{wallet_address[:12]}",
                "role": role,
            },
        )
        if created:
            _ensure_account_exists_on_testnet(wallet_address)

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserProfileSerializer(user).data,
                "created": created,
            },
            status=status.HTTP_200_OK,
        )


class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Retrieve or update authenticated user profile",
        tags=["Authentication"],
    )
    def get_object(self):
        return self.request.user


# Bounty views


class BountyListView(generics.ListAPIView):
    serializer_class = BountyListSerializer
    permission_classes = [permissions.AllowAny]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["skill_category", "status", "reward_asset"]
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "reward_amount", "deadline"]
    ordering = ["-created_at"]

    @extend_schema(
        summary="List bounties with search and category filtering",
        tags=["Bounties"],
    )
    def get_queryset(self):
        return Bounty.objects.select_related("poster", "contributor").all()


class BountyPrepareFundView(APIView):
    """
    POST /api/v1/bounties/prepare-fund/
    Build an unsigned transaction XDR for the poster to sign with Rabet/Freighter.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Prepare unsigned funding transaction for wallet signing",
        description="Returns unsigned XDR for the poster's wallet to sign on Stellar Testnet.",
        request=BountyCreateSerializer,
        responses={200: OpenApiResponse(description="Unsigned XDR envelope and network info")},
        tags=["Bounties"],
    )
    def post(self, request):
        poster_address = request.user.wallet_address
        amount = request.data.get("reward_amount")
        asset_code = request.data.get("reward_asset", "XLM")
        deadline_str = request.data.get("deadline")

        if not amount:
            return Response(
                {"detail": "reward_amount is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from dateutil.parser import parse as parse_date
            deadline_dt = parse_date(deadline_str)
            deadline_ts = int(deadline_dt.timestamp())
        except Exception:
            deadline_ts = int(time.time()) + 7 * 86400

        # Contract requires deadline >= current_time + 86400 (24h)
        min_ts = int(time.time()) + 86400 + 60
        if deadline_ts < min_ts:
            deadline_ts = min_ts

        try:
            _ensure_account_exists_on_testnet(poster_address)

            unsigned_xdr = build_fund_escrow_tx(
                poster_address=poster_address,
                amount=str(amount),
                asset_code=asset_code,
                deadline_ts=deadline_ts,
            )
            return Response(
                {
                    "xdr": unsigned_xdr,
                    "poster_address": poster_address,
                    "network_passphrase": settings.STELLAR_NETWORK_PASSPHRASE,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"Failed to prepare funding transaction: {e}")
            return Response(
                {"detail": f"Failed to build funding transaction: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )


class BountyCreateView(generics.CreateAPIView):
    """
    POST /api/v1/bounties/create/
    Create a bounty and lock funds on Stellar Testnet.

    Accepted funding modes (in priority order):
    1. ``signed_xdr`` – the poster signs the XDR with their wallet (production).
    2. No payload  → server-side escrow relayer on Testnet (demo / debug only).

    Only verified signed XDR broadcasts or testnet server funding are accepted.
    """

    serializer_class = BountyCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        user = self.request.user
        if user.role != User.Role.POSTER:
            user.role = User.Role.POSTER
            user.save(update_fields=["role"])

        serializer.save(poster=user)

    @extend_schema(
        summary="Create a new funded bounty with on-chain escrow lock",
        tags=["Bounties"],
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.perform_create(serializer)
        bounty = serializer.instance

        if not bounty.escrow_bounty_id:
            bounty.escrow_bounty_id = str(bounty.id.int % 1000000 + 1)
            bounty.save(update_fields=["escrow_bounty_id"])

        escrow_address = _get_escrow_address()

        signed_xdr = request.data.get("signed_xdr")

        tx_hash = None
        tx_status = Transaction.Status.SUCCESS

        # 1. Poster submitted a signed (and prepared) XDR from their wallet
        if signed_xdr:
            logger.info(f"Broadcasting client-signed funding XDR for bounty {bounty.id}...")
            sub_res = submit_transaction(signed_xdr)
            if sub_res.get("successful"):
                tx_hash = sub_res.get("hash")
                bounty.escrow_tx_hash = tx_hash
                onchain_count = get_onchain_bounty_count()
                if onchain_count > 0:
                    bounty.escrow_bounty_id = str(onchain_count)
                bounty.save(update_fields=["escrow_tx_hash", "escrow_bounty_id"])
            else:
                logger.error(
                    f"Client-signed XDR failed for bounty {bounty.id}: {sub_res.get('result')}"
                )
                tx_status = Transaction.Status.FAILED
                Transaction.objects.create(
                    bounty=bounty,
                    tx_hash=None,
                    tx_type=Transaction.TxType.FUND_ESCROW,
                    from_address=request.user.wallet_address,
                    to_address=escrow_address,
                    amount=bounty.reward_amount,
                    asset=bounty.reward_asset,
                    status=tx_status,
                )
                bounty.delete()
                err_info = sub_res.get("result", {})
                err_msg = (
                    err_info.get("detail") or err_info.get("error") or str(err_info)
                    if isinstance(err_info, dict)
                    else str(err_info)
                )
                return Response(
                    {"detail": f"On-chain funding transaction failed: {err_msg}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 2. No client signature → server-side escrow relayer (demo / Testnet only)
        else:
            if not (settings.DEBUG and _is_testnet()):
                bounty.delete()
                return Response(
                    {"detail": "A client-signed funding transaction (signed_xdr) is required to lock escrow funds."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            logger.info(f"Executing on-chain testnet escrow funding for bounty {bounty.id}...")
            server_fund = execute_server_escrow_funding(
                bounty_id=bounty.escrow_bounty_id,
                poster_address=request.user.wallet_address,
                amount=str(bounty.reward_amount),
                asset_code=bounty.reward_asset,
                deadline_ts=int(bounty.deadline.timestamp()),
                memo_text=f"fund:{str(bounty.id)[:20]}",
            )
            if server_fund.get("successful"):
                tx_hash = server_fund.get("hash")
                bounty.escrow_tx_hash = tx_hash
                onchain_count = get_onchain_bounty_count()
                if onchain_count > 0:
                    bounty.escrow_bounty_id = str(onchain_count)
                bounty.save(update_fields=["escrow_tx_hash", "escrow_bounty_id"])
            else:
                logger.error(f"On-chain funding failed for bounty {bounty.id}: {server_fund.get('result')}")
                tx_status = Transaction.Status.FAILED
                Transaction.objects.create(
                    bounty=bounty,
                    tx_hash=None,
                    tx_type=Transaction.TxType.FUND_ESCROW,
                    from_address=request.user.wallet_address,
                    to_address=escrow_address,
                    amount=bounty.reward_amount,
                    asset=bounty.reward_asset,
                    status=tx_status,
                )
                bounty.delete()
                err_info = server_fund.get("result", {})
                err_msg = (
                    err_info.get("detail") or err_info.get("error") or str(err_info)
                    if isinstance(err_info, dict)
                    else str(err_info)
                )
                return Response(
                    {"detail": f"On-chain escrow funding failed: {err_msg}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        Transaction.objects.create(
            bounty=bounty,
            tx_hash=tx_hash or None,
            tx_type=Transaction.TxType.FUND_ESCROW,
            from_address=request.user.wallet_address,
            to_address=escrow_address,
            amount=bounty.reward_amount,
            asset=bounty.reward_asset,
            status=tx_status,
        )

        return Response(
            BountyDetailSerializer(bounty).data,
            status=status.HTTP_201_CREATED,
        )


class BountyDetailView(generics.RetrieveAPIView):
    serializer_class = BountyDetailSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "pk"

    def get_queryset(self):
        return Bounty.objects.select_related("poster", "contributor").prefetch_related(
            "submissions", "transactions"
        )


class BountyClaimView(APIView):
    """
    POST /api/v1/bounties/<pk>/claim/
    Claim an open bounty. Uses select_for_update() to prevent race conditions.
    """

    serializer_class = BountyDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Claim an open bounty",
        responses={
            200: BountyDetailSerializer,
            400: OpenApiResponse(description="Already claimed, expired, or invalid request"),
            403: OpenApiResponse(description="Forbidden — poster or wrong role"),
        },
        tags=["Bounties"],
    )
    def post(self, request, pk):
        # Only contributors can claim bounties.
        if request.user.role == User.Role.POSTER:
            return Response(
                {"detail": "Posters cannot claim bounties. Switch to a contributor account."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Lock bounty record to prevent race conditions on simultaneous claims.
        with db_transaction.atomic():
            try:
                bounty = Bounty.objects.select_for_update().select_related("poster").get(pk=pk)
            except Bounty.DoesNotExist:
                return Response({"detail": "Bounty not found."}, status=status.HTTP_404_NOT_FOUND)

            if bounty.poster == request.user:
                return Response(
                    {"detail": "You cannot claim your own bounty."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if bounty.status != Bounty.Status.POSTED:
                return Response(
                    {"detail": f"Bounty cannot be claimed — current status: {bounty.get_status_display()}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if bounty.is_expired:
                return Response(
                    {"detail": "This bounty has expired."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            bounty.contributor = request.user
            bounty.status = Bounty.Status.CLAIMED
            bounty.save(update_fields=["contributor", "status", "updated_at"])

        return Response(BountyDetailSerializer(bounty).data, status=status.HTTP_200_OK)


class SubmissionCreateView(APIView):
    """POST /api/v1/bounties/<pk>/submit/ — Submit completed work for a claimed bounty."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Submit completed work for a claimed bounty",
        request=SubmissionCreateSerializer,
        responses={
            201: SubmissionSerializer,
            400: OpenApiResponse(description="Invalid status or missing fields"),
            403: OpenApiResponse(description="Not the assigned contributor"),
        },
        tags=["Submissions"],
    )
    def post(self, request, pk):
        try:
            bounty = Bounty.objects.get(pk=pk)
        except Bounty.DoesNotExist:
            return Response({"detail": "Bounty not found."}, status=status.HTTP_404_NOT_FOUND)

        if bounty.contributor != request.user:
            return Response(
                {"detail": "Only the assigned contributor can submit work."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if bounty.status not in (Bounty.Status.CLAIMED, Bounty.Status.SUBMITTED):
            return Response(
                {"detail": f"Cannot submit work — bounty status: {bounty.get_status_display()}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SubmissionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with db_transaction.atomic():
            submission = Submission.objects.create(
                bounty=bounty,
                contributor=request.user,
                **serializer.validated_data,
            )
            bounty.status = Bounty.Status.SUBMITTED
            bounty.save(update_fields=["status", "updated_at"])

        return Response(SubmissionSerializer(submission).data, status=status.HTTP_201_CREATED)


class SubmissionReviewView(APIView):
    """POST /api/v1/bounties/<pk>/review/ — Review a submission (approve or request revision)."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Review a contributor submission",
        request=SubmissionReviewSerializer,
        responses={
            200: OpenApiResponse(description="Review outcome and optional tx hash"),
            400: OpenApiResponse(description="No submission to review"),
            403: OpenApiResponse(description="Not the bounty poster"),
            500: OpenApiResponse(description="Payment release failed"),
        },
        tags=["Submissions"],
    )
    def post(self, request, pk):
        try:
            bounty = Bounty.objects.select_related("poster", "contributor").get(pk=pk)
        except Bounty.DoesNotExist:
            return Response({"detail": "Bounty not found."}, status=status.HTTP_404_NOT_FOUND)

        if bounty.poster != request.user:
            return Response(
                {"detail": "Only the bounty poster can review submissions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if bounty.status != Bounty.Status.SUBMITTED:
            return Response(
                {"detail": f"No submission to review — bounty status: {bounty.get_status_display()}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SubmissionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data["action"]
        reviewer_notes = serializer.validated_data.get("reviewer_notes", "")

        submission = bounty.submissions.order_by("-created_at").first()
        if not submission:
            return Response(
                {"detail": "No submission found for this bounty."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if action == ReviewAction.REQUEST_REVISION:
            with db_transaction.atomic():
                submission.status = Submission.Status.REVISION_REQUESTED
                submission.reviewer_notes = reviewer_notes
                submission.save(update_fields=["status", "reviewer_notes", "updated_at"])

                bounty.status = Bounty.Status.CLAIMED
                bounty.save(update_fields=["status", "updated_at"])

            return Response(
                {"detail": "Revision requested.", "reviewer_notes": reviewer_notes},
                status=status.HTTP_200_OK,
            )

        # Attempt on-chain payment before updating status.
        escrow_address = _get_escrow_address()

        try:
            signed_xdr = build_release_payment_tx(
                bounty_id=bounty.escrow_bounty_id or str(bounty.id),
                contributor_address=bounty.contributor.wallet_address,
                poster_address=bounty.poster.wallet_address,
                amount=str(bounty.reward_amount),
                asset_code=bounty.reward_asset,
                memo_text=str(bounty.id)[:28],
            )
            result = submit_transaction(signed_xdr)

            if result["successful"]:
                # Payment confirmed — now commit the DB state atomically
                with db_transaction.atomic():
                    submission.status = Submission.Status.APPROVED
                    submission.reviewer_notes = reviewer_notes
                    submission.save(update_fields=["status", "reviewer_notes", "updated_at"])

                    bounty.status = Bounty.Status.PAID
                    bounty.payment_tx_hash = result["hash"]
                    bounty.save(update_fields=["status", "payment_tx_hash", "updated_at"])

                    Transaction.objects.create(
                        bounty=bounty,
                        tx_hash=result.get("hash") or None,
                        tx_type=Transaction.TxType.RELEASE_PAYMENT,
                        from_address=escrow_address,
                        to_address=bounty.contributor.wallet_address,
                        amount=bounty.reward_amount,
                        asset=bounty.reward_asset,
                        status=Transaction.Status.SUCCESS,
                    )

                return Response(
                    {
                        "detail": "Submission approved. Payment released on-chain.",
                        "tx_hash": result["hash"],
                        "explorer_url": f"https://stellar.expert/explorer/testnet/tx/{result['hash']}",
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                # Payment failed — record the failure but leave bounty in SUBMITTED
                # so the poster can retry.
                Transaction.objects.create(
                    bounty=bounty,
                    tx_hash=result.get("hash") or None,
                    tx_type=Transaction.TxType.RELEASE_PAYMENT,
                    from_address=escrow_address,
                    to_address=bounty.contributor.wallet_address,
                    amount=bounty.reward_amount,
                    asset=bounty.reward_asset,
                    status=Transaction.Status.FAILED,
                )
                return Response(
                    {
                        "detail": (
                            "Payment failed on-chain. The submission has NOT been marked as approved. "
                            "Please retry the review action."
                        ),
                        "error": result.get("result"),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
        except Exception as e:
            logger.error(f"Payment release failed: {e}")
            return Response(
                {"detail": f"Payment release failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BountyExpireView(APIView):
    """POST /api/v1/bounties/<pk>/expire/ — Expire a bounty and refund the poster."""

    serializer_class = BountyDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Expire a bounty and refund escrowed funds to the poster",
        responses={
            200: OpenApiResponse(description="Refund tx hash"),
            400: OpenApiResponse(description="Already expired/paid, deadline not passed, or wrong status"),
            403: OpenApiResponse(description="Not the poster or admin"),
        },
        tags=["Bounties"],
    )
    def post(self, request, pk):
        try:
            bounty = Bounty.objects.select_related("poster").get(pk=pk)
        except Bounty.DoesNotExist:
            return Response({"detail": "Bounty not found."}, status=status.HTTP_404_NOT_FOUND)

        if bounty.poster != request.user and not request.user.is_staff:
            return Response(
                {"detail": "Only the poster or an admin can expire a bounty."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Only POSTED and CLAIMED bounties may be expired.
        if bounty.status not in (Bounty.Status.POSTED, Bounty.Status.CLAIMED):
            return Response(
                {
                    "detail": (
                        f"Bounty cannot be expired — current status: {bounty.get_status_display()}. "
                        "Only POSTED or CLAIMED bounties may be expired."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not bounty.is_expired:
            return Response(
                {"detail": "Bounty deadline has not passed yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        escrow_address = _get_escrow_address()

        try:
            signed_xdr = build_refund_tx(
                bounty_id=bounty.escrow_bounty_id or str(bounty.id),
                poster_address=bounty.poster.wallet_address,
                amount=str(bounty.reward_amount),
                asset_code=bounty.reward_asset,
                memo_text=f"refund:{str(bounty.id)[:20]}",
            )
            result = submit_transaction(signed_xdr)

            if result["successful"]:
                with db_transaction.atomic():
                    bounty.status = Bounty.Status.EXPIRED
                    bounty.save(update_fields=["status", "updated_at"])

                    Transaction.objects.create(
                        bounty=bounty,
                        tx_hash=result.get("hash") or None,
                        tx_type=Transaction.TxType.REFUND,
                        from_address=escrow_address,
                        to_address=bounty.poster.wallet_address,
                        amount=bounty.reward_amount,
                        asset=bounty.reward_asset,
                        status=Transaction.Status.SUCCESS,
                    )

                return Response(
                    {"detail": "Bounty expired. Funds refunded to poster.", "tx_hash": result["hash"]},
                    status=status.HTTP_200_OK,
                )
            else:
                Transaction.objects.create(
                    bounty=bounty,
                    tx_hash=result.get("hash") or None,
                    tx_type=Transaction.TxType.REFUND,
                    from_address=escrow_address,
                    to_address=bounty.poster.wallet_address,
                    amount=bounty.reward_amount,
                    asset=bounty.reward_asset,
                    status=Transaction.Status.FAILED,
                )
                return Response(
                    {
                        "detail": (
                            "On-chain refund transaction failed. The bounty has NOT been marked as expired. "
                            "Please retry the refund."
                        ),
                        "error": result.get("result"),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
        except Exception as e:
            logger.error(f"Refund failed: {e}")
            return Response(
                {"detail": f"Refund failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# Dashboard views


class ContributorDashboardView(APIView):
    """GET /api/v1/dashboard/contributor/ — Contributor's active claims, submissions, and earnings."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Contributor dashboard",
        responses={200: ContributorDashboardSerializer},
        tags=["Dashboards"],
    )
    def get(self, request):
        user = request.user
        active_claims = Bounty.objects.filter(contributor=user, status=Bounty.Status.CLAIMED).select_related("poster")
        submitted = Bounty.objects.filter(contributor=user, status=Bounty.Status.SUBMITTED).select_related("poster")
        completed = Bounty.objects.filter(contributor=user, status=Bounty.Status.PAID).select_related("poster")
        total_earned = completed.aggregate(total=Sum("reward_amount"))["total"] or Decimal("0")

        return Response(
            {
                "active_claims": BountyListSerializer(active_claims, many=True).data,
                "submitted": BountyListSerializer(submitted, many=True).data,
                "completed": BountyListSerializer(completed, many=True).data,
                "total_earned": str(total_earned),
            },
            status=status.HTTP_200_OK,
        )


class PosterDashboardView(APIView):
    """GET /api/v1/dashboard/poster/ — Poster's bounty portfolio and spend summary."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Poster dashboard",
        responses={200: PosterDashboardSerializer},
        tags=["Dashboards"],
    )
    def get(self, request):
        user = request.user
        all_bounties = Bounty.objects.filter(poster=user).select_related("contributor")

        active = all_bounties.filter(status=Bounty.Status.POSTED)
        claimed = all_bounties.filter(status=Bounty.Status.CLAIMED)
        pending_review = all_bounties.filter(status=Bounty.Status.SUBMITTED)
        completed = all_bounties.filter(status=Bounty.Status.PAID)
        total_spent = completed.aggregate(total=Sum("reward_amount"))["total"] or Decimal("0")

        return Response(
            {
                "active_bounties": BountyListSerializer(active, many=True).data,
                "claimed_bounties": BountyListSerializer(claimed, many=True).data,
                "pending_reviews": BountyListSerializer(pending_review, many=True).data,
                "completed_bounties": BountyListSerializer(completed, many=True).data,
                "total_posted": all_bounties.count(),
                "total_spent": str(total_spent),
            },
            status=status.HTTP_200_OK,
        )


# Transaction views


class TransactionListView(generics.ListAPIView):
    """GET /api/v1/transactions/<bounty_id>/ — List on-chain transactions for a bounty.

    Restricted to authenticated participants (poster or contributor).
    """

    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="List on-chain transactions for a bounty",
        tags=["Transactions"],
    )
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Transaction.objects.none()
        bounty_id = self.kwargs.get("bounty_id")

        # Restrict access to the bounty poster and contributor only
        try:
            bounty = Bounty.objects.get(pk=bounty_id)
        except Bounty.DoesNotExist:
            return Transaction.objects.none()

        user = self.request.user
        if bounty.poster != user and bounty.contributor != user and not user.is_staff:
            return Transaction.objects.none()

        return Transaction.objects.filter(bounty_id=bounty_id)