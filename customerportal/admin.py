from django.contrib import admin
from .models import SellerBooking, SellerPortalSession


@admin.register(SellerBooking)
class SellerBookingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "seller",
        "sender_phone",
        "total_pc",
        "status",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "seller__name",
        "seller__code",
        "sender_phone",
        "sender_address",
    )


@admin.register(SellerPortalSession)
class SellerPortalSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "seller",
        "user",
        "login_at",
        "logout_at",
        "last_activity_at",
        "session_minutes",
        "ip_address",
    )
    list_filter = (
        "login_at",
        "logout_at",
        "seller",
    )
    search_fields = (
        "seller__name",
        "seller__code",
        "user__username",
        "ip_address",
    )
    readonly_fields = (
        "seller",
        "user",
        "login_at",
        "logout_at",
        "last_activity_at",
        "ip_address",
        "user_agent",
    )

    def session_minutes(self, obj):
        return obj.duration_minutes
    session_minutes.short_description = "Minutes"