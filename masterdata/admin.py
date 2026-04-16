from django.contrib import admin
from .models import Seller, Shipper, SellerPriceRule


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "name",
        "phone",
        "is_active",
        "portal_user",
        "created_at",
    )
    search_fields = ("code", "name", "phone")
    list_filter = ("is_active",)
    ordering = ("-id",)


@admin.register(Shipper)
class ShipperAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "name",
        "phone",
        "shipper_type",
        "is_active",
        "portal_user",
        "created_at",
    )
    search_fields = ("code", "name", "phone")
    list_filter = ("shipper_type", "is_active")
    ordering = ("-id",)


@admin.register(SellerPriceRule)
class SellerPriceRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "seller",
        "shipper",
        "rule_type",
        "delivery_fee",
        "additional_fee",
        "percent_display",
        "is_locked",
        "is_active",
        "created_at",
    )
    search_fields = (
        "seller__name",
        "seller__code",
        "shipper__name",
        "shipper__code",
    )
    list_filter = (
        "rule_type",
        "shipper",
        "is_locked",
        "is_active",
    )
    ordering = ("seller__name", "shipper__name", "rule_type")

    fieldsets = (
        (
            "Main",
            {
                "fields": (
                    "seller",
                    "shipper",
                    "rule_type",
                    "delivery_fee",
                    "additional_fee",
                    "percent_cod",
                )
            },
        ),
        (
            "Options",
            {
                "fields": (
                    "is_locked",
                    "is_active",
                )
            },
        ),
    )

    def percent_display(self, obj):
        return obj.percent_cod
    percent_display.short_description = "Percent Price / COD"