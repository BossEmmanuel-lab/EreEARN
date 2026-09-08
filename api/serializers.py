from datetime import timedelta
from django.db import models
from django.utils import timezone
from rest_framework import serializers
from .models import Bounty, Submission, Transaction, User

# Minimum deadline duration must match the contract (24 hours).
MIN_DEADLINE_DURATION = timedelta(hours=24)


class ReviewAction(models.TextChoices):
    APPROVE = "approve", "Approve & Pay"
    REQUEST_REVISION = "request_revision", "Request Revision"


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "wallet_address", "role", "bio", "avatar_url", "date_joined"]
        read_only_fields = fields


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "wallet_address", "role", "bio", "avatar_url", "date_joined", "updated_at"]
        read_only_fields = ["id", "wallet_address", "date_joined", "updated_at"]


class WalletChallengeSerializer(serializers.Serializer):
    wallet_address = serializers.CharField(max_length=56, help_text="Stellar public key (G...)")

    def validate_wallet_address(self, value):
        if not value.startswith("G") or len(value) != 56:
            raise serializers.ValidationError(
                "Invalid Stellar public key. Must start with 'G' and be 56 characters"
            )
        return value


class WalletVerifySerializer(serializers.Serializer):
    wallet_address = serializers.CharField(max_length=56)
    signature = serializers.CharField(help_text="Hex-encoded Ed25519 signature of the challenge hash")
    role = serializers.ChoiceField(
        choices=User.Role.choices,
        default=User.Role.CONTRIBUTOR,
        help_text="Role to register with (only used for first-time login)",
    )


class BountyListSerializer(serializers.ModelSerializer):
    poster = UserSerializer(read_only=True)
    contributor = UserSerializer(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Bounty
        fields = [
            "id",
            "title",
            "skill_category",
            "reward_amount",
            "reward_asset",
            "deadline",
            "status",
            "escrow_tx_hash",
            "poster",
            "contributor",
            "is_expired",
            "created_at",
        ]
        read_only_fields = fields


from drf_spectacular.utils import extend_schema_field


class SubmissionSerializer(serializers.ModelSerializer):
    contributor = UserSerializer(read_only=True)

    class Meta:
        model = Submission
        fields = [
            "id",
            "bounty",
            "contributor",
            "submission_text",
            "submission_url",
            "reviewer_notes",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TransactionSerializer(serializers.ModelSerializer):
    explorer_url = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            "id",
            "bounty",
            "tx_hash",
            "tx_type",
            "from_address",
            "to_address",
            "amount",
            "asset",
            "status",
            "explorer_url",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_explorer_url(self, obj):
        if obj.tx_hash:
            return f"https://stellar.expert/explorer/testnet/tx/{obj.tx_hash}"
        return None


class BountyDetailSerializer(serializers.ModelSerializer):
    poster = UserSerializer(read_only=True)
    contributor = UserSerializer(read_only=True)
    submissions = serializers.SerializerMethodField()
    transactions = serializers.SerializerMethodField()
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Bounty
        fields = [
            "id",
            "poster",
            "title",
            "description",
            "skill_category",
            "reward_amount",
            "reward_asset",
            "deadline",
            "status",
            "escrow_tx_hash",
            "escrow_bounty_id",
            "payment_tx_hash",
            "contributor",
            "submissions",
            "transactions",
            "is_expired",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(SubmissionSerializer(many=True))
    def get_submissions(self, obj):
        qs = obj.submissions.all()
        return SubmissionSerializer(qs, many=True).data

    @extend_schema_field(TransactionSerializer(many=True))
    def get_transactions(self, obj):
        qs = obj.transactions.all()
        return TransactionSerializer(qs, many=True).data


class BountyCreateSerializer(serializers.ModelSerializer):
    signed_xdr = serializers.CharField(
        required=False, allow_blank=True, write_only=True, default=""
    )

    class Meta:
        model = Bounty
        fields = [
            "title",
            "description",
            "skill_category",
            "reward_amount",
            "reward_asset",
            "deadline",
            "escrow_tx_hash",
            "signed_xdr",
        ]

    def validate_deadline(self, value):
        if value < timezone.now() + MIN_DEADLINE_DURATION:
            raise serializers.ValidationError("Deadline must be at least 24 hours from now.")
        return value

    def validate_reward_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Reward amount must be greater than zero.")
        return value

    def create(self, validated_data):
        validated_data.pop("signed_xdr", None)
        validated_data.pop("escrow_tx_hash", None)
        validated_data["status"] = Bounty.Status.POSTED
        return super().create(validated_data)


class SubmissionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Submission
        fields = ["submission_text", "submission_url"]

    def validate(self, attrs):
        if not attrs.get("submission_text") and not attrs.get("submission_url"):
            raise serializers.ValidationError(
                "You must provide either a description or a URL for your submission"
            )
        return attrs


class SubmissionReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=ReviewAction.choices,
        help_text="Action to take on the submission.",
    )
    reviewer_notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Feedback for the contributor.",
    )

    def validate(self, attrs):
        if attrs["action"] == ReviewAction.REQUEST_REVISION and not attrs.get("reviewer_notes"):
            raise serializers.ValidationError(
                {"reviewer_notes": "Notes are required when requesting a revision."}
            )
        return attrs


class ContributorDashboardSerializer(serializers.Serializer):
    active_claims = BountyListSerializer(many=True)
    submitted = BountyListSerializer(many=True)
    completed = BountyListSerializer(many=True)
    total_earned = serializers.DecimalField(max_digits=20, decimal_places=7)


class PosterDashboardSerializer(serializers.Serializer):
    active_bounties = BountyListSerializer(many=True)
    claimed_bounties = BountyListSerializer(many=True)
    pending_reviews = BountyListSerializer(many=True)
    completed_bounties = BountyListSerializer(many=True)
    total_posted = serializers.IntegerField()
    total_spent = serializers.DecimalField(max_digits=20, decimal_places=7)