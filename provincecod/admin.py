from decimal import Decimal

from django.contrib import admin
from django.db.models import Count, Sum

from .models import ProvinceCODBatch, ProvinceCODItem


class ProvinceCODItemInline(admin.TabularInline):
    model = ProvinceCODItem
    extra = 0
    show_change_link = True

    fields = [
        "order",
        "original_cod",
        "province_fee",
        "carrier_fee",
        "net_cod",
        "cod_status",
        "seller_settled",
        "sent_at",
        "received_at",
        "paid_at",
        "returned_at",
    ]

    readonly_fields = [
        "order",
        "original_cod",
        "province_fee",
        "carrier_fee",
        "net_cod",
        "sent_at",
        "received_at",
        "paid_at",
        "returned_at",
    ]


@admin.register(ProvinceCODBatch)
class ProvinceCODBatchAdmin(admin.ModelAdmin):
    list_display = [
        "batch_number",
        "shipper",
        "status",
        "item_count",
        "total_original_cod",
        "assigned_at",
        "sent_at",
        "sent_by",
        "created_by",
        "created_at",
    ]

    list_filter = [
        "status",
        "shipper",
        "created_at",
    ]

    search_fields = [
        "=id",
        "remark",
        "shipper__name",
        "created_by__username",
        "sent_by__username",
        "items__order__tracking_no",
    ]

    readonly_fields = [
        "created_at",
        "updated_at",
    ]

    date_hierarchy = "created_at"
    inlines = [ProvinceCODItemInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "shipper",
                "created_by",
                "sent_by",
                "cancelled_by",
            )
            .annotate(
                admin_item_count=Count("items", distinct=True),
                admin_total_original_cod=Sum("items__original_cod"),
            )
        )

    @admin.display(description="Batch", ordering="id")
    def batch_number(self, obj):
        return f"PVCOD-{obj.pk}"

    @admin.display(description="Items", ordering="admin_item_count")
    def item_count(self, obj):
        return obj.admin_item_count

    @admin.display(
        description="Original COD",
        ordering="admin_total_original_cod",
    )
    def total_original_cod(self, obj):
        return obj.admin_total_original_cod or Decimal("0.00")


@admin.register(ProvinceCODItem)
class ProvinceCODItemAdmin(admin.ModelAdmin):
    list_display = [
        "order",
        "batch",
        "original_cod",
        "province_fee",
        "carrier_fee",
        "net_cod",
        "cod_status",
        "seller_settled",
        "created_at",
    ]

    list_filter = [
        "cod_status",
        "seller_settled",
        "batch__shipper",
        "created_at",
    ]

    search_fields = [
        "order__tracking_no",
        "order__receiver_name",
        "order__receiver_phone",
        "order__seller__name",
        "carrier_reference",
        "received_person",
        "return_reason",
        "note",
    ]

    readonly_fields = [
        "created_at",
        "updated_at",
    ]

    date_hierarchy = "created_at"

    list_select_related = [
        "batch",
        "batch__shipper",
        "order",
        "order__seller",
    ]
