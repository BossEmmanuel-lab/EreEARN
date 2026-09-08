import uuid
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user model with Stellar wallet integration."""

    class Role(models.TextChoices):
        POSTER = "POSTER", "Poster"
        CONTRIBUTOR = "CONTRIBUTOR", "Contributor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet_address = models.CharField(max_length=56, unique=True, db_index=True, help_text="Stellar public key (G...)")
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.CONTRIBUTOR)
    bio = models.TextField(blank=True, default="")
    avatar_url = models.URLField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    REQUIRED_FIELDS = ["wallet_address"]

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return f"{self.get_role_display()} — {self.wallet_address[:8]}"




class Bounty(models.Model):
    class Status(models.TextChoices):
        POSTED = "POSTED", "Posted"
        CLAIMED = "CLAIMED", "Claimed"
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        PAID = "PAID", "Paid"
        EXPIRED = "EXPIRED", "Expired"

    class SkillCategory(models.TextChoices):
        DEVELOPMENT = "DEVELOPMENT", "Development"
        DESIGN = "DESIGN", "Design"
        WRITING = "WRITING", "Writing"
        VIDEO = "VIDEO", "Video Creation"
        PROJECT_MANAGEMENT = "PROJECT_MANAGEMENT", "Project Management"
        COMMUNITY = "COMMUNITY", "Community"

    class Asset(models.TextChoices):
        XLM = "XLM", "XLM"
        USDC = "USDC", "USDC"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poster = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posted_bounties")
    title = models.CharField(max_length=255)
    description = models.TextField()
    skill_category = models.CharField(max_length=20,choices=SkillCategory.choices)
    reward_amount = models.DecimalField(max_digits=20,decimal_places=7, help_text="Amount in the chosen asset (7 decimals for Stellar precision)")
    reward_asset = models.CharField(max_length=4,choices=Asset.choices,default=Asset.XLM,)
    deadline = models.DateTimeField()
    status = models.CharField(max_length=10,choices=Status.choices,default=Status.POSTED,db_index=True)

    escrow_tx_hash = models.CharField(max_length=64, blank=True, default="", help_text="Stellar tx hash for the escrow funding transaction")
    escrow_bounty_id = models.CharField(max_length=128, blank=True, default="", help_text="On-chain bounty ID from the Soroban contract")
    payment_tx_hash = models.CharField(max_length=64, blank=True, default="", help_text="Stellar tx hash for the payout transaction")
    contributor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name="claimed_bounties")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "bounties"

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @property
    def is_expired(self):
        return self.deadline < timezone.now() and self.status not in (
            self.Status.APPROVED,
            self.Status.PAID,
            self.Status.EXPIRED,
        )




class Submission(models.Model):
    """Deliverable submitted by a contributor for a claimed bounty."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Review"
        APPROVED = "APPROVED", "Approved"
        REVISION_REQUESTED = "REVISION_REQUESTED", "Revision Requested"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bounty = models.ForeignKey(Bounty, on_delete=models.CASCADE, related_name="submissions")
    contributor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submissions")
    submission_text = models.TextField(help_text="Description of the completed work")
    submission_url = models.URLField(blank=True, default="", help_text="Link to the deliverable (GitHub PR, Figma, Google Doc, etc)")
    reviewer_notes = models.TextField(blank=True, default="", help_text="Feedback from the poster")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f'Submission for "{self.bounty.title}" by {self.contributor}'




class Transaction(models.Model):
    """On-chain Stellar transaction log for a bounty."""

    class TxType(models.TextChoices):
        FUND_ESCROW = "FUND_ESCROW", "Fund Escrow"
        RELEASE_PAYMENT = "RELEASE_PAYMENT", "Release Payment"
        REFUND = "REFUND", "Refund"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bounty = models.ForeignKey(Bounty, on_delete=models.CASCADE, related_name="transactions")
    tx_hash = models.CharField(max_length=64, blank=True, null=True, unique=True, db_index=True)
    tx_type = models.CharField(max_length=16, choices=TxType.choices)
    from_address = models.CharField(max_length=56)
    to_address = models.CharField(max_length=56)
    amount = models.DecimalField(max_digits=20, decimal_places=7)
    asset = models.CharField(max_length=4)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        hash_str = self.tx_hash[:12] if self.tx_hash else "pending"
        return f"{self.get_tx_type_display()} — {hash_str}"
