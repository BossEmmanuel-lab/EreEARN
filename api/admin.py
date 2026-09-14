from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Bounty, Submission, Transaction, User



@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["username", "email", "wallet_address", "role", "is_staff", "date_joined"]
    list_filter = ["role", "is_staff", "is_active"]
    search_fields = ["username", "email", "wallet_address"]
    ordering = ["-date_joined"]

    fieldsets = list(BaseUserAdmin.fieldsets) + [
        ("ÉréEARN Profile", {"fields": ("wallet_address", "role", "skills", "bio", "avatar_url")})
    ]
    add_fieldsets = list(BaseUserAdmin.add_fieldsets) + [
        ("ÉréEARN Profile", {"fields": ("wallet_address", "role", "skills")})
    ]



@admin.register(Bounty)
class BountyAdmin(admin.ModelAdmin):
    list_display = ["title", "poster", "skill_category", "reward_amount", "reward_asset", "status", "deadline","created_at"]
    list_filter = ["status", "skill_category", "reward_asset"]
    search_fields = ["title", "description", "poster__wallet_address"]
    raw_id_fields = ["poster", "contributor"]
    readonly_fields = ["id", "escrow_tx_hash", "escrow_bounty_id", "payment_tx_hash", "created_at","updated_at"]
    ordering = ["-created_at"]



@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ["bounty", "contributor", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["bounty__title", "contributor__wallet_address","submission_text"]
    raw_id_fields = ["bounty", "contributor"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]




@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ["tx_hash_short","bounty","tx_type","amount","asset","status", "created_at"]
    list_filter = ["tx_type", "status", "asset"]
    search_fields = ["tx_hash", "from_address", "to_address"]
    raw_id_fields = ["bounty"]
    readonly_fields = ["id", "created_at"]
    ordering = ["-created_at"]

    @admin.display(description="TX Hash")
    def tx_hash_short(self, obj):
        if obj.tx_hash:
            return f"{obj.tx_hash[:12]}"
        return "—"
