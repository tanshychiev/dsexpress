from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.utils import timezone

from masterdata.models import Shipper


ZERO = Decimal("0.00")
MONEY_STEP = Decimal("0.01")


def money(value):
    try:
        return Decimal(str(value or 0)).quantize(
            MONEY_STEP,
            rounding=ROUND_HALF_UP,
        )
    except Exception:
        return ZERO


# =========================================================
# PROVINCE COD BATCH
# =========================================================
class ProvinceCODBatch(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_SENT = "SENT"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    created_at = models.DateTimeField(default=timezone.now)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="province_cod_batches_created",
    )

    shipper = models.ForeignKey(
        Shipper,
        on_delete=models.PROTECT,
        related_name="province_cod_batches",
        null=True,
        blank=True,
    )

    assigned_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="province_cod_batches_sent",
        null=True,
        blank=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="province_cod_batches_cancelled",
        null=True,
        blank=True,
    )

    remark = models.TextField(
        blank=True,
        default="",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"PVCOD-{self.id}"


# =========================================================
# PROVINCE COD ITEM
# =========================================================
class ProvinceCODItem(models.Model):
    STATUS_SENT = "SENT"
    STATUS_RECEIVED = "RECEIVED"
    STATUS_PAID = "PAID"
    STATUS_RETURNED = "RETURNED"

    STATUS_CHOICES = [
        (STATUS_SENT, "Sent"),
        (STATUS_RECEIVED, "Received"),
        (STATUS_PAID, "Paid"),
        (STATUS_RETURNED, "Returned"),
    ]

    METHOD_CALL = "CALL"
    METHOD_TRACKING = "TRACKING"
    METHOD_CARRIER = "CARRIER"
    METHOD_OTHER = "OTHER"

    CONFIRMATION_METHOD_CHOICES = [
        (METHOD_CALL, "Phone Call"),
        (METHOD_TRACKING, "Tracking"),
        (METHOD_CARRIER, "Carrier Report"),
        (METHOD_OTHER, "Other"),
    ]

    batch = models.ForeignKey(
        ProvinceCODBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="province_cod_items",
    )

    # Order values before Province COD assignment.
    original_cod = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
    )

    status_before = models.CharField(
        max_length=30,
        blank=True,
        default="",
    )

    # First fee deducted when Complete Sent is clicked.
    province_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
    )

    # Saved carrier pricing for later payment.
    carrier_fixed_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
    )

    # Example: 0.01 means 1%.
    carrier_percent_rate = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        default=Decimal("0.000000"),
    )

    # Actual fee confirmed when J&T pays DS Express.
    carrier_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
    )

    # Final amount payable to seller after J&T payment.
    net_cod = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
    )

    # Blank before the batch is completed.
    cod_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )

    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    # Customer received confirmation.
    received_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    received_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="province_cod_received_items",
        null=True,
        blank=True,
    )

    received_person = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    confirmation_method = models.CharField(
        max_length=20,
        choices=CONFIRMATION_METHOD_CHOICES,
        blank=True,
        default="",
    )

    # J&T/bus paid DS Express.
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    paid_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="province_cod_paid_items",
        null=True,
        blank=True,
    )

    carrier_reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    # Returned parcel.
    returned_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    returned_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="province_cod_returned_items",
        null=True,
        blank=True,
    )

    return_reason = models.TextField(
        blank=True,
        default="",
    )

    # DS Express transferred the final COD to seller.
    seller_settled = models.BooleanField(
        default=False,
        db_index=True,
    )

    seller_settled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    seller_settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="province_cod_settled_items",
        null=True,
        blank=True,
    )

    note = models.TextField(
        blank=True,
        default="",
    )

    created_at = models.DateTimeField(
        default=timezone.now,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-id"]

        constraints = [
            models.UniqueConstraint(
                fields=["batch", "order"],
                name="unique_province_cod_batch_order",
            ),
        ]

        indexes = [
            models.Index(fields=["cod_status", "sent_at"]),
            models.Index(fields=["seller_settled", "cod_status"]),
        ]

    def __str__(self):
        return (
            f"{self.batch_id} - "
            f"{self.order.tracking_no} - "
            f"{self.cod_status or 'NOT SENT'}"
        )

    @property
    def seller(self):
        return self.order.seller

    @property
    def shipper(self):
        return self.batch.shipper

    def suggested_carrier_fee(self):
        percentage_fee = money(
            self.original_cod * self.carrier_percent_rate
        )

        return money(
            self.carrier_fixed_fee + percentage_fee
        )

    def calculate_net_cod(self):
        return money(
            self.original_cod - self.carrier_fee
        )

    def save(self, *args, **kwargs):
        self.original_cod = money(self.original_cod)
        self.province_fee = money(self.province_fee)
        self.carrier_fixed_fee = money(self.carrier_fixed_fee)
        self.carrier_fee = money(self.carrier_fee)

        if self.cod_status == self.STATUS_PAID:
            self.net_cod = self.calculate_net_cod()

        elif self.cod_status == self.STATUS_RETURNED:
            self.net_cod = ZERO
            self.seller_settled = False
            self.seller_settled_at = None
            self.seller_settled_by = None

        else:
            # SENT and RECEIVED are not financially completed.
            self.net_cod = ZERO

        super().save(*args, **kwargs)