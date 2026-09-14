import uuid
from datetime import timedelta
from django.db import models
from django.utils import timezone
from rest_framework import serializers
from .models import Bounty, Submission, Transaction, User

MIN_DEADLINE_DURATION = timedelta(hours=24)


class ReviewAction(models.TextChoices):
    APPROVE = "approve", "Approve & Pay"
    REQUEST_REVISION = "request_revision", "Request Revision"


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "wallet_address",
            "role",
            "bio",
            "avatar_url",
            "skills",
            "date_joined",
        ]
        read_only_fields = fields


class UserProfileSerializer(serializers.ModelSerializer):
    skills = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
    )
    wallet_address = serializers.CharField(
        max_length=56,
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "wallet_address",
            "role",
            "bio",
            "avatar_url",
            "skills",
            "date_joined",
            "updated_at",
        ]
        read_only_fields = ["id", "date_joined", "updated_at"]

    def validate_wallet_address(self, value):
        if not value:
            return None
        value = value.strip()
        if not value.startswith("G") or len(value) != 56:
            raise serializers.ValidationError("Invalid Stellar public key. Must start with 'G' and be 56 characters.")
        existing = User.objects.filter(wallet_address=value)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError("This wallet address is already linked to another account.")
        return value

    def validate_skills(self, value):
        if isinstance(value, list):
            return [str(s).strip() for s in value if str(s).strip()]
        return []


class UserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, required=False, allow_blank=True)
    skills = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
    )
    wallet_address = serializers.CharField(
        max_length=56,
        required=False,
        allow_null=True,
        allow_blank=True,
        default=None,
    )

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "password",
            "password_confirm",
            "role",
            "skills",
            "bio",
            "avatar_url",
            "wallet_address",
        ]
        extra_kwargs = {
            "email": {"required": True},
            "username": {"required": False},
        }

    def validate_email(self, value):
        if not value:
            raise serializers.ValidationError("Email is required.")
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_wallet_address(self, value):
        if not value:
            return None
        value = value.strip()
        if not value.startswith("G") or len(value) != 56:
            raise serializers.ValidationError("Invalid Stellar public key. Must start with 'G' and be 56 characters.")
        if User.objects.filter(wallet_address=value).exists():
            raise serializers.ValidationError("This wallet address is already in use.")
        return value

    def validate(self, attrs):
        password = attrs.get("password")
        password_confirm = attrs.get("password_confirm")
        if password_confirm and password != password_confirm:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm", None)
        password = validated_data.pop("password")
        email = validated_data.get("email")
        username = validated_data.get("username")
        if not username:
            base_username = email.split("@")[0]
            candidate = base_username[:20]
            if User.objects.filter(username=candidate).exists():
                candidate = f"{candidate}_{uuid.uuid4().hex[:6]}"
            validated_data["username"] = candidate

        user = User.objects.create_user(password=password, **validated_data)
        return user


class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get("email", "").strip().lower()
        password = attrs.get("password", "")

        user = User.objects.filter(email__iexact=email).first()
        if not user or not user.check_password(password):
            raise serializers.ValidationError("Invalid email or password.")

        if not user.is_active:
            raise serializers.ValidationError("User account is disabled.")

        attrs["user"] = user
        return attrs


class AuthResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserProfileSerializer()
    created = serializers.BooleanField(required=False)


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
    username = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
        help_text="Optional custom username for first-time login",
    )

    def validate_username(self, value):
        if not value:
            return None
        value = value.strip()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value


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