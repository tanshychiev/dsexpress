from django.contrib import admin

from .models import PickupBooking, PickupBookingAudit


@admin.register(PickupBooking)
class PickupBookingAdmin(admin.ModelAdmin):
    list_display = ("order", "method", "cod", "delivery_fee", "additional_fee", "completed_by", "completed_at")
    search_fields = ("order__tracking_no", "order__seller__name", "order__seller_order_code")
    list_filter = ("method", "completed_at")


@admin.register(PickupBookingAudit)
class PickupBookingAuditAdmin(admin.ModelAdmin):
    list_display = ("booking", "action", "changed_by", "changed_at")
    search_fields = ("booking__order__tracking_no",)
    list_filter = ("action", "changed_at")
