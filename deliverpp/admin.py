from django.contrib import admin
from .models import SystemSetting, ClearPPCOD, PPDeliveryItem, PPDeliveryBatch



@admin.register(ClearPPCOD)
class ClearPPCODAdmin(admin.ModelAdmin):
    list_display = ("batch", "is_balanced", "diff_khr", "target_total_usd", "input_total_usd", "finalized_at")
    search_fields = ("batch__code",)


# optional: show tick fields quickly
@admin.register(PPDeliveryItem)
class PPDeliveryItemAdmin(admin.ModelAdmin):
    list_display = ("id", "batch", "order", "source_type", "ticked", "delivery_cleared_at", "cod_cleared_at")
    list_filter = ("ticked", "source_type")

@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("usd_to_khr_rate", "balance_tolerance_khr", "updated_at", "updated_by")    