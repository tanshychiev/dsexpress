from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import Account


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "login_username",
        "account_type",
        "seller",
        "shipper",
        "user_is_active",
        "password_status",
        "created_at",
    )
    list_filter = ("account_type", "created_at", "updated_at")
    search_fields = (
        "user__username",
        "seller__name",
        "shipper__name",
    )
    readonly_fields = (
        "user_admin_link",
        "login_username",
        "password_status",
        "password_help",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = ("user", "seller", "shipper")

    fieldsets = (
        ("Login Account", {
            "fields": (
                "user",
                "user_admin_link",
                "login_username",
                "password_status",
                "password_help",
            )
        }),
        ("Portal Link", {
            "fields": (
                "account_type",
                "seller",
                "shipper",
            )
        }),
        ("System", {
            "fields": (
                "created_at",
                "updated_at",
            )
        }),
    )

    def login_username(self, obj):
        return obj.user.username if obj and obj.user_id else "-"

    login_username.short_description = "Username"

    def user_is_active(self, obj):
        return bool(obj.user.is_active) if obj and obj.user_id else False

    user_is_active.boolean = True
    user_is_active.short_description = "Active"

    def password_status(self, obj):
        return "Hidden / encrypted"

    password_status.short_description = "Password"

    def password_help(self, obj):
        return (
            "Django does not store the real password text, so it cannot be viewed. "
            "Open the linked User account and set a new password when a seller/customer forgets it."
        )

    password_help.short_description = "Password reset note"

    def user_admin_link(self, obj):
        if not obj or not obj.user_id:
            return "-"

        meta = obj.user._meta
        url = reverse(
            f"admin:{meta.app_label}_{meta.model_name}_change",
            args=[obj.user_id],
        )

        return format_html(
            '<a href="{}" target="_blank">Open user: {} / reset password</a>',
            url,
            obj.user.username,
        )

    user_admin_link.short_description = "User admin"
