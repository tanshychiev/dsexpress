from django.contrib import admin

from .models import ProvinceCODBatch, ProvinceCODItem


class ProvinceCODItemInline(admin.TabularInline):
    model = ProvinceCODItem
    extra = 0
    readonly_fields = [
        "order",
        "original_cod",
        "province_fee",
        "cod_status",
        "sent_at",
        "received_at",
        "paid_at",
        "returned_at",
    ]


@admin.register(ProvinceCODBatch)
class ProvinceCODBatchAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "shipper",
        "status",
        "assigned_at",
        "sent_at",
        "created_by",
    ]

    list_filter = [
        "status",
        "shipper",
    ]

    search_fields = [
        "id",
        "remark",
    ]

    inlines = [
        ProvinceCODItemInline,
    ]


@admin.register(ProvinceCODItem)
class ProvinceCODItemAdmin(admin.ModelAdmin):
    list_display = [
        "order",
        "batch",
        "original_cod",
        "cod_status",
        "carrier_fee",
        "net_cod",
        "seller_settled",
    ]

    list_filter = [
        "cod_status",
        "seller_settled",
        "batch__shipper",
    ]

    search_fields = [
        "order__tracking_no",
        "order__receiver_name",
        "order__receiver_phone",
        "order__seller__name",
        "carrier_reference",
    ]

    readonly_fields = [
        "created_at",
        "updated_at",
    ]