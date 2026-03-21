from django.contrib import admin
from .models import Seller, Shipper


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "phone", "is_active", "portal_user", "created_at")
    search_fields = ("code", "name", "phone")
    list_filter = ("is_active",)
    ordering = ("-id",)


@admin.register(Shipper)
class ShipperAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "phone", "shipper_type", "is_active", "portal_user", "created_at")
    search_fields = ("code", "name", "phone")
    list_filter = ("shipper_type", "is_active")
    ordering = ("-id",)