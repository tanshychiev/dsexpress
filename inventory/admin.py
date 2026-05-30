from django.contrib import admin

from .models import (
    InventorySellerSetting,
    OrderStockLink,
    StockAlias,
    StockMovement,
    StockProduct,
    StockSnapshot,
)


@admin.register(InventorySellerSetting)
class InventorySellerSettingAdmin(admin.ModelAdmin):
    list_display = [
        "seller",
        "stock_mode",
        "show_stock_in_portal",
        "updated_at",
    ]
    list_filter = [
        "stock_mode",
        "show_stock_in_portal",
    ]
    search_fields = [
        "seller__name",
        "seller__code",
    ]


@admin.register(StockProduct)
class StockProductAdmin(admin.ModelAdmin):
    list_display = [
        "seller",
        "name",
        "product_type",
        "sku",
        "location",
        "is_active",
        "created_at",
    ]
    list_filter = [
        "is_active",
        "product_type",
        "seller",
    ]
    search_fields = [
        "seller__name",
        "seller__code",
        "name",
        "sku",
        "product_type",
        "location",
    ]


@admin.register(StockAlias)
class StockAliasAdmin(admin.ModelAdmin):
    list_display = [
        "seller",
        "alias_text",
        "product",
        "created_at",
    ]
    search_fields = [
        "seller__name",
        "seller__code",
        "alias_text",
        "product__name",
        "product__sku",
    ]


@admin.register(StockSnapshot)
class StockSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        "seller",
        "product",
        "confirmed_qty",
        "confirmed_at",
        "confirmed_by",
    ]
    list_filter = [
        "confirmed_at",
        "seller",
    ]
    search_fields = [
        "seller__name",
        "seller__code",
        "product__name",
        "product__sku",
        "note",
    ]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "seller",
        "product",
        "movement_type",
        "qty_delta",
        "order",
        "created_by",
    ]
    list_filter = [
        "movement_type",
        "created_at",
        "seller",
    ]
    search_fields = [
        "seller__name",
        "seller__code",
        "product__name",
        "product__sku",
        "order__tracking_no",
        "note",
    ]


@admin.register(OrderStockLink)
class OrderStockLinkAdmin(admin.ModelAdmin):
    list_display = [
        "order",
        "seller",
        "product",
        "status",
        "quantity",
        "reserved_qty",
        "shortage_qty",
        "updated_at",
    ]
    list_filter = [
        "status",
        "seller",
    ]
    search_fields = [
        "order__tracking_no",
        "seller__name",
        "seller__code",
        "product__name",
        "product__sku",
        "raw_product_text",
    ]