from decimal import Decimal

from django.conf import settings
from django.db import models


class ReturnShopBatch(models.Model):
    # Status constants
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
        related_name="returnshop_batches",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    remark = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["status", "assigned_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"RTS-{self.id}"

    # ============================================================
    # Progress helpers
    # ============================================================
    def get_progress_counts(self, done_codes=None):
        """
        Progress is based on child labels, not batch.items.

        Rules:
        - If batch itself is CANCELLED => UI should show CANCELLED
        - If no labels => PENDING
        - If done = 0 => PENDING
        - If 0 < done < total => X/Y DONE
        - If done == total => DONE

        done_codes:
            set of ReturnShopLabel.code that are already completed/ticked in Deliver PP
        """
        done_codes = set(done_codes or [])

        labels_qs = getattr(self, "_prefetched_objects_cache", {}).get("labels")
        if labels_qs is None:
            labels_qs = self.labels.all()

        labels = list(labels_qs)
        total_count = len(labels)

        done_count = 0
        for lb in labels:
            code = (getattr(lb, "code", "") or "").strip()
            if code and code in done_codes:
                done_count += 1

        return {
            "total_count": total_count,
            "done_count": done_count,
            "pending_count": max(total_count - done_count, 0),
        }

    def get_progress_label(self, done_codes=None):
        if self.status == self.STATUS_CANCELLED:
            return self.STATUS_CANCELLED

        counts = self.get_progress_counts(done_codes=done_codes)
        total_count = counts["total_count"]
        done_count = counts["done_count"]

        if total_count <= 0:
            return self.STATUS_PENDING

        if done_count <= 0:
            return self.STATUS_PENDING

        if done_count >= total_count:
            return self.STATUS_DONE

        return f"{done_count}/{total_count} DONE"


class ReturnShopBatchItem(models.Model):
    """
    One Order inside a ReturnShopBatch.
    Save old COD/status so you can undo complete/cancel safely.
    """
    batch = models.ForeignKey(
        ReturnShopBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="returnshop_batch_items",
    )

    cod_before = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status_before = models.CharField(max_length=30, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "order"], name="uniq_rts_batch_order"),
        ]
        indexes = [
            models.Index(fields=["batch", "id"]),
            models.Index(fields=["order"]),
        ]

    def __str__(self):
        return f"{self.batch} / {getattr(self.order, 'tracking_no', self.order_id)}"


class ReturnShopLabel(models.Model):
    """
    Master label batch.
    - MERGE: merged selected shops into 1 label batch
    - SHOP: no-merge auto label per shop
    """
    MODE_MERGE = "MERGE"
    MODE_SHOP = "SHOP"

    MODE_CHOICES = [
        (MODE_MERGE, "MERGE"),
        (MODE_SHOP, "SHOP"),
    ]

    batch = models.ForeignKey(
        ReturnShopBatch,
        on_delete=models.CASCADE,
        related_name="labels",
    )

    # Stable for QR / search in history
    code = models.CharField(max_length=50, unique=True, blank=True, default="")

    # Printed info on label
    ship_to_address = models.CharField(max_length=255, blank=True, default="")
    ship_to_phone = models.CharField(max_length=50, blank=True, default="")
    cod_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # Needed for Undo buttons + display
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_MERGE)
    shop_name = models.CharField(max_length=255, blank=True, default="")  # "Shop A + Shop B" or single shop

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["batch", "created_at"]),
            models.Index(fields=["mode"]),
            models.Index(fields=["code"]),
        ]

    def __str__(self):
        return self.code or f"RTS-{self.batch_id}-{self.id}"


class ReturnShopLabelItem(models.Model):
    """
    Link label -> batch item
    NOTE: created_at is nullable to avoid migration default issue
    """
    label = models.ForeignKey(
        ReturnShopLabel,
        on_delete=models.CASCADE,
        related_name="items",
    )
    batch_item = models.ForeignKey(
        ReturnShopBatchItem,
        on_delete=models.PROTECT,
        related_name="label_items",
    )

    # Keep nullable to avoid "provide default" migration error if you already have rows
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["label", "batch_item"], name="uniq_rts_label_batchitem"),
        ]
        indexes = [
            models.Index(fields=["label"]),
            models.Index(fields=["batch_item"]),
        ]

    def __str__(self):
        return f"{self.label} / item:{self.batch_item_id}"