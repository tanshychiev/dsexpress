from django.contrib import admin
from .models import Order, ImportBatch


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("tracking_no", "seller", "receiver_phone", "price", "cod", "status", "created_at")
    list_filter = ("status", "seller")
    search_fields = ("tracking_no", "receiver_phone", "seller_order_code")
    readonly_fields = ("created_at",)


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "filename", "created_at")
