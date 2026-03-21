from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from masterdata.models import Shipper
from orders.models import Order

User = get_user_model()


class PPDeliveryBatch(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_DONE = "DONE"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "PENDING"),
        (STATUS_DONE, "DONE"),
        (STATUS_CANCELLED, "CANCELLED"),
    ]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pp_batches",
    )
    shipper = models.ForeignKey(
        Shipper,
        on_delete=models.PROTECT,
        related_name="pp_batches",
        null=True,
        blank=True,
    )

    remark = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    assigned_at = models.DateTimeField(null=True, blank=True)

    total_count = models.PositiveIntegerField(default=0)
    shipment_count = models.PositiveIntegerField(default=0)
    return_batch_count = models.PositiveIntegerField(default=0)
    total_pc = models.PositiveIntegerField(default=0)

    return_batch_ids = models.JSONField(default=list, blank=True)
    return_label_codes = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    @property
    def code(self) -> str:
        d = (self.created_at or timezone.now()).strftime("%Y%m%d")
        return f"PP{d}{self.id:05d}"

    def recalc_totals(self, save: bool = False):
        total_pc = self.items.count()
        shipment_cnt = self.items.filter(source_type=PPDeliveryItem.SOURCE_NORMAL).count()

        master_ids = []
        for x in (self.return_batch_ids or []):
            try:
                master_ids.append(int(x))
            except Exception:
                continue

        return_batch_cnt = len(set(master_ids))

        self.total_pc = int(total_pc)
        self.shipment_count = int(shipment_cnt)
        self.return_batch_count = int(return_batch_cnt)
        self.total_count = int(self.shipment_count + self.return_batch_count)

        if save:
            self.save(update_fields=["total_pc", "shipment_count", "return_batch_count", "total_count"])

    def __str__(self) -> str:
        return self.code


class PPDeliveryItem(models.Model):
    SOURCE_NORMAL = "NORMAL"
    SOURCE_RETURN = "RETURN"

    SOURCE_TYPE_CHOICES = [
        (SOURCE_NORMAL, "NORMAL"),
        (SOURCE_RETURN, "RETURN"),
    ]

    batch = models.ForeignKey(PPDeliveryBatch, on_delete=models.CASCADE, related_name="items")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="pp_items")

    source_type = models.CharField(max_length=10, choices=SOURCE_TYPE_CHOICES, default=SOURCE_NORMAL)
    source_code = models.CharField(max_length=50, blank=True, default="", db_index=True)

    ticked = models.BooleanField(default=False)
    delivery_cleared_at = models.DateTimeField(null=True, blank=True)
    cod_cleared_at = models.DateTimeField(null=True, blank=True)

    tick_locked = models.BooleanField(default=False)

    cod_snapshot = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    reason = models.CharField(max_length=255, blank=True, default="")
    note = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        unique_together = [("batch", "order")]
        indexes = [
            models.Index(fields=["batch", "source_type"]),
            models.Index(fields=["source_code"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk is None and (self.cod_snapshot is None or self.cod_snapshot == Decimal("0.00")):
            try:
                self.cod_snapshot = self.order.cod or Decimal("0.00")
            except Exception:
                self.cod_snapshot = Decimal("0.00")

        if self.source_type == self.SOURCE_RETURN:
            self.cod_snapshot = Decimal("0.00")

        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.batch_id} - {self.order.tracking_no}"


class SystemSetting(models.Model):
    usd_to_khr_rate = models.PositiveIntegerField(default=4100)
    balance_tolerance_khr = models.PositiveIntegerField(default=99)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deliverpp_settings_updated",
    )

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(
            id=1,
            defaults={
                "usd_to_khr_rate": 4100,
                "balance_tolerance_khr": 99,
            },
        )
        return obj

    def __str__(self):
        return f"SystemSetting(rate={self.usd_to_khr_rate}, tol={self.balance_tolerance_khr})"


class ClearPPCOD(models.Model):
    batch = models.OneToOneField(
        PPDeliveryBatch,
        on_delete=models.CASCADE,
        related_name="clear_cod",
    )
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    finalized_at = models.DateTimeField(null=True, blank=True)

    cash_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cash_khr = models.PositiveIntegerField(default=0)
    aba_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    aba_khr = models.PositiveIntegerField(default=0)
    expense = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    note = models.TextField(blank=True, default="")

    target_total_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    input_total_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    diff_khr = models.IntegerField(default=0)
    is_balanced = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ClearCOD {self.batch.code} balanced={self.is_balanced} diff_khr={self.diff_khr}"