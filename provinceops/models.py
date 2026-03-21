# provinceops/models.py
from django.conf import settings
from django.db import models
from django.utils import timezone

from masterdata.models import Shipper


class ProvinceBatch(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_DONE = "DONE"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_DONE, "Done"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="province_batches_created",
    )

    shipper = models.ForeignKey(Shipper, on_delete=models.PROTECT, null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)

    remark = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    def __str__(self):
        return f"PV-{self.id}"


class ProvinceBatchItem(models.Model):
    batch = models.ForeignKey(ProvinceBatch, on_delete=models.CASCADE, related_name="items")

    # ✅ FIX: no import, use string reference
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT)

    cod_before = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status_before = models.CharField(max_length=30, blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("batch", "order")]

    def __str__(self):
        return f"{self.batch_id} - {self.order_id}"