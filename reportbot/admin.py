from django.contrib import admin
from .models import ShopDailyReport, ShopDailyReportStatusLog


@admin.register(ShopDailyReport)
class ShopDailyReportAdmin(admin.ModelAdmin):
    list_display = (
        "report_code",
        "shop",
        "report_date",
        "done_count",
        "pending_count",
        "total_cod",
        "total_fee",
        "total_pay",
        "status",
    )
    list_filter = ("status", "report_date")
    search_fields = ("report_code", "shop__name", "shop__code")


@admin.register(ShopDailyReportStatusLog)
class ShopDailyReportStatusLogAdmin(admin.ModelAdmin):
    list_display = ("report", "old_status", "new_status", "emoji", "actor_name", "created_at")
    list_filter = ("new_status", "created_at")