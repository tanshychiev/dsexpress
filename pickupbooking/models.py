from decimal import Decimal

from django.conf import settings
from django.db import models

from orders.models import Order


class PickupBooking(models.Model):
    METHOD_CUSTOMER = "CUSTOMER_PICKUP"
    METHOD_GRAB = "GRAB"
    METHOD_CHOICES = [
        (METHOD_CUSTOMER, "Customer Pickup"),
        (METHOD_GRAB, "Grab / External Rider"),
    ]

    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="pickup_booking",
    )
    method = models.CharField(max_length=30, choices=METHOD_CHOICES)
    cod = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    additional_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pickup_bookings_completed",
    )
    completed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pickup_bookings_updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-completed_at", "-id"]
        verbose_name = "Pickup / Booking"
        verbose_name_plural = "Pickup / Booking"

    def __str__(self):
        return f"{self.order.tracking_no} - {self.get_method_display()}"


class PickupBookingAudit(models.Model):
    ACTION_COMPLETED = "COMPLETED"
    ACTION_UPDATED = "UPDATED"
    ACTION_CHOICES = [
        (ACTION_COMPLETED, "Completed"),
        (ACTION_UPDATED, "Updated"),
    ]

    booking = models.ForeignKey(
        PickupBooking,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pickup_booking_audits",
    )
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-changed_at", "-id"]
