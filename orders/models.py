from __future__ import annotations

import json
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from masterdata.models import Seller, Shipper


# ============================================================
# IMPORT BATCH
# ============================================================

class ImportBatch(models.Model):
    filename = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"ImportBatch {self.id} - {self.filename}"


# ============================================================
# BULK UPDATE BATCH
# ============================================================

class BulkUpdateBatch(models.Model):
    filename = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"BulkUpdateBatch {self.id} - {self.filename}"


# ============================================================
# ORDER SETTINGS
# ============================================================

class OrderSetting(models.Model):
    usd_to_khr = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("4100"),
        help_text="Exchange rate: 1 USD = ? KHR",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Order Setting"
        verbose_name_plural = "Order Settings"

    def __str__(self) -> str:
        return f"Rate: 1$ = {self.usd_to_khr}៛"

    @staticmethod
    def get_rate() -> Decimal:
        obj = OrderSetting.objects.first()
        if not obj:
            obj = OrderSetting.objects.create(usd_to_khr=Decimal("4100"))
        return obj.usd_to_khr


# ============================================================
# ORDER
# ============================================================

class Order(models.Model):
    STATUS_CREATED = "CREATED"
    STATUS_INBOUND = "INBOUND"
    STATUS_OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    STATUS_DELIVERED = "DELIVERED"
    STATUS_RETURNING = "RETURNING"
    STATUS_RETURNED = "RETURNED"
    STATUS_PROVINCE_ASSIGNED = "PROVINCE_ASSIGNED"
    STATUS_RETURN_ASSIGNED = "RETURN_ASSIGNED"
    STATUS_VOID = "VOID"

    STATUS_CHOICES = [
        (STATUS_CREATED, "CREATED"),
        (STATUS_INBOUND, "INBOUND"),
        (STATUS_OUT_FOR_DELIVERY, "DELIVERING"),
        (STATUS_DELIVERED, "DELIVERED"),
        (STATUS_RETURNING, "RETURNING"),
        (STATUS_RETURNED, "RETURNED"),
        (STATUS_PROVINCE_ASSIGNED, "PROVINCE_ASSIGNED"),
        (STATUS_RETURN_ASSIGNED, "RETURN_ASSIGNED"),
        (STATUS_VOID, "VOID"),
    ]

    tracking_no = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
    )

    seller = models.ForeignKey(
        Seller,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    seller_code = models.CharField(max_length=50, blank=True, default="")
    seller_name = models.CharField(max_length=255, blank=True, null=True)
    seller_order_code = models.CharField(max_length=100, blank=True, null=True)

    product_desc = models.CharField(max_length=255, blank=True, null=True)
    quantity = models.IntegerField(default=1)

    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    cod = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    additional_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    province_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    receiver_name = models.CharField(max_length=200, blank=True, null=True)
    receiver_phone = models.CharField(max_length=50, blank=True, null=True)
    receiver_address = models.TextField(blank=True, null=True)

    remark = models.TextField(blank=True, null=True)
    reason = models.TextField(blank=True, null=True)

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_CREATED,
        db_index=True,
    )

    delivery_shipper = models.ForeignKey(
        Shipper,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="delivery_orders",
    )

    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )

    print_count = models.PositiveIntegerField(default=0)

    # soft delete
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(blank=True, null=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_orders",
    )

    # lock protection
    is_locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(blank=True, null=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locked_orders",
    )

    # report / operation fields
    done_at = models.DateField(blank=True, null=True, db_index=True)
    clear_delivery = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(blank=True, null=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="updated_orders",
    )

    class Meta:
        ordering = ["-id"]

    def soft_delete(self, user=None):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

    def lock(self, user=None):
        self.is_locked = True
        self.locked_at = timezone.now()
        self.locked_by = user
        self.save(update_fields=["is_locked", "locked_at", "locked_by"])

    def unlock(self):
        self.is_locked = False
        self.locked_at = None
        self.locked_by = None
        self.save(update_fields=["is_locked", "locked_at", "locked_by"])

    def save(self, *args, **kwargs):
        if self.status == self.STATUS_DELIVERED and not self.done_at:
            self.done_at = timezone.localdate()

        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.tracking_no or f"Order {self.id}"


# ============================================================
# BULK UPDATE ROW
# ============================================================

class BulkUpdateRow(models.Model):
    batch = models.ForeignKey(
        BulkUpdateBatch,
        on_delete=models.CASCADE,
        related_name="rows",
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="bulk_update_rows",
    )

    tracking_no = models.CharField(max_length=50, blank=True, default="")
    status = models.CharField(max_length=30, blank=True, default="")

    before_json = models.TextField(null=True, blank=True)
    after_json = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def before(self):
        try:
            return json.loads(self.before_json or "{}")
        except Exception:
            return {}

    def after(self):
        try:
            return json.loads(self.after_json or "{}")
        except Exception:
            return {}

    def __str__(self) -> str:
        return f"BulkUpdateRow #{self.id} - {self.tracking_no}"


# ============================================================
# ORDER ACTIVITY (TIMELINE)
# ============================================================

class OrderActivity(models.Model):
    ACTION_CREATE = "create"
    ACTION_EDIT = "edit"
    ACTION_INBOUND = "inbound"
    ACTION_ASSIGN = "assign"
    ACTION_OUT_FOR_DELIVERY = "out_for_delivery"
    ACTION_DELIVERED = "delivered"
    ACTION_RETURNED = "returned"
    ACTION_RETURN_ASSIGNED = "return_assigned"
    ACTION_VOID = "void"
    ACTION_DELETE = "delete"
    ACTION_RESTORE = "restore"
    ACTION_LOCK = "lock"
    ACTION_UNLOCK = "unlock"
    ACTION_PRINT = "print"

    ACTION_CHOICES = [
        (ACTION_CREATE, "Create"),
        (ACTION_EDIT, "Edit"),
        (ACTION_INBOUND, "Inbound"),
        (ACTION_ASSIGN, "Assign"),
        (ACTION_OUT_FOR_DELIVERY, "Out For Delivery"),
        (ACTION_DELIVERED, "Delivered"),
        (ACTION_RETURNED, "Returned"),
        (ACTION_RETURN_ASSIGNED, "Return Assigned"),
        (ACTION_VOID, "Void"),
        (ACTION_DELETE, "Delete"),
        (ACTION_RESTORE, "Restore"),
        (ACTION_LOCK, "Lock"),
        (ACTION_UNLOCK, "Unlock"),
        (ACTION_PRINT, "Print"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="activities",
    )

    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    old_status = models.CharField(max_length=30, blank=True, default="")
    new_status = models.CharField(max_length=30, blank=True, default="")

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_activity_actor",
    )

    shipper = models.ForeignKey(
        Shipper,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_activity_shipper",
    )

    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.order.tracking_no} | {self.action} | {self.created_at}"


# ============================================================
# AUDIT LOG
# ============================================================

class AuditLog(models.Model):
    MODULE_ORDER = "order"
    MODULE_IMPORT = "import"
    MODULE_BULK_UPDATE = "bulk_update"
    MODULE_CLEAR_PP = "clearpp"
    MODULE_RETURN = "return"

    MODULE_CHOICES = [
        (MODULE_ORDER, "Order"),
        (MODULE_IMPORT, "Import"),
        (MODULE_BULK_UPDATE, "Bulk Update"),
        (MODULE_CLEAR_PP, "Clear PP"),
        (MODULE_RETURN, "Return"),
    ]

    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_RESTORE = "restore"
    ACTION_LOCK = "lock"
    ACTION_UNLOCK = "unlock"
    ACTION_ASSIGN_SHIPPER = "assign_shipper"
    ACTION_CHANGE_STATUS = "change_status"
    ACTION_IMPORT = "import"
    ACTION_ROLLBACK_IMPORT = "rollback_import"
    ACTION_PRINT = "print"

    ACTION_CHOICES = [
        (ACTION_CREATE, "Create"),
        (ACTION_UPDATE, "Update"),
        (ACTION_DELETE, "Delete"),
        (ACTION_RESTORE, "Restore"),
        (ACTION_LOCK, "Lock"),
        (ACTION_UNLOCK, "Unlock"),
        (ACTION_ASSIGN_SHIPPER, "Assign Shipper"),
        (ACTION_CHANGE_STATUS, "Change Status"),
        (ACTION_IMPORT, "Import"),
        (ACTION_ROLLBACK_IMPORT, "Rollback Import"),
        (ACTION_PRINT, "Print"),
    ]

    module = models.CharField(max_length=50, choices=MODULE_CHOICES)
    object_id = models.PositiveIntegerField()
    object_repr = models.CharField(max_length=255)

    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    field_name = models.CharField(max_length=100, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    note = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="order_audit_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.module} | {self.action} | {self.object_repr}"