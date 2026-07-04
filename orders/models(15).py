from django.conf import settings
from django.db import models
from django.utils import timezone

from masterdata.models import Seller


class SellerBooking(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_ACCEPTED = "ACCEPTED"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE)
    sender_phone = models.CharField(max_length=50)
    sender_address = models.TextField()
    total_pc = models.IntegerField(default=1)
    remark = models.TextField(blank=True)
    pickup_date = models.DateField()
    pickup_time = models.CharField(max_length=50)
    arrive_date = models.DateField()
    arrive_time = models.CharField(max_length=50)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    telegram_chat_id = models.CharField(max_length=100, blank=True, null=True)
    telegram_message_id = models.CharField(max_length=100, blank=True, null=True)
    processed_by_telegram_name = models.CharField(max_length=255, blank=True, null=True)
    processed_by_telegram_id = models.CharField(max_length=100, blank=True, null=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Booking {self.id} - {self.status}"


class SellerPortalRole(models.Model):
    """A role owned by one seller/shop."""

    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="portal_roles",
    )
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True, default="")

    # Example: {"orders.view": true, "inventory.manage": false}
    permissions = models.JSONField(default=dict, blank=True)

    is_recommended = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["seller__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["seller", "name"],
                name="unique_seller_portal_role_name",
            ),
        ]

    def __str__(self):
        return f"{self.seller.name} - {self.name}"


class SellerPortalSession(models.Model):
    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="portal_sessions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seller_portal_sessions",
    )

    login_at = models.DateTimeField(default=timezone.now)
    logout_at = models.DateTimeField(blank=True, null=True)
    last_activity_at = models.DateTimeField(default=timezone.now)

    # Safe additive fields for page/time tracking.
    active_seconds = models.PositiveBigIntegerField(default=0)
    last_page_key = models.CharField(max_length=150, blank=True, default="")
    last_page_name = models.CharField(max_length=180, blank=True, default="")

    ip_address = models.CharField(max_length=100, blank=True, default="")
    user_agent = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-login_at"]
        indexes = [
            models.Index(fields=["seller", "user", "logout_at"]),
            models.Index(fields=["seller", "last_activity_at"]),
        ]

    def __str__(self):
        return f"{self.seller.name} | {self.login_at:%Y-%m-%d %H:%M:%S}"

    @property
    def duration_minutes(self):
        # Use measured active time when available. Fall back to the old logic
        # for existing historical sessions.
        if self.active_seconds:
            return max(int(self.active_seconds // 60), 0)

        end_time = self.logout_at or self.last_activity_at or timezone.now()
        delta = end_time - self.login_at
        return max(int(delta.total_seconds() // 60), 0)


class SellerPortalDailyUsage(models.Model):
    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="portal_daily_usage",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seller_portal_daily_usage",
    )
    usage_date = models.DateField()
    active_seconds = models.PositiveBigIntegerField(default=0)
    page_views = models.PositiveIntegerField(default=0)
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-usage_date", "seller__name", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["seller", "user", "usage_date"],
                name="unique_seller_user_daily_usage",
            ),
        ]
        indexes = [
            models.Index(fields=["seller", "usage_date"]),
            models.Index(fields=["user", "usage_date"]),
        ]

    def __str__(self):
        return f"{self.seller.name} - {self.user.username} - {self.usage_date}"


class SellerPortalPageUsage(models.Model):
    daily_usage = models.ForeignKey(
        SellerPortalDailyUsage,
        on_delete=models.CASCADE,
        related_name="pages",
    )
    page_key = models.CharField(max_length=150)
    page_name = models.CharField(max_length=180, blank=True, default="")
    active_seconds = models.PositiveBigIntegerField(default=0)
    page_views = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-active_seconds", "page_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["daily_usage", "page_key"],
                name="unique_daily_portal_page_usage",
            ),
        ]

    def __str__(self):
        return f"{self.daily_usage} - {self.page_name or self.page_key}"


class SellerPortalAuditLog(models.Model):
    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="portal_audit_logs",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="seller_portal_actions",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="seller_portal_target_actions",
    )
    action = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    ip_address = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["seller", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self):
        return f"{self.seller.name} - {self.action} - {self.created_at:%Y-%m-%d %H:%M}"


# =========================================================
# SELLER ORDER UPLOAD - PENDING APPROVAL
# =========================================================

class SellerUploadBatch(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_IMPORTED = "IMPORTED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_IMPORTED, "Imported"),
    ]

    TEMPLATE_KEY = "DS_EXPRESS_UPLOAD_TEMPLATE_V1"

    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="upload_batches",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seller_order_uploads",
    )

    file = models.FileField(
        upload_to="seller_order_uploads/%Y/%m/",
        blank=True,
        null=True,
    )
    original_filename = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)

    reject_reason = models.TextField(blank=True, default="")

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_seller_uploads",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rejected_seller_uploads",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)

    imported_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["seller", "status", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    @property
    def code(self):
        return f"SUP-{self.id:06d}"

    @property
    def can_approve(self):
        return (
            self.status == self.STATUS_PENDING
            and self.total_rows > 0
            and self.error_rows == 0
            and self.duplicate_rows == 0
        )

    def __str__(self):
        return f"{self.code} - {self.seller.name} - {self.status}"


class SellerUploadRow(models.Model):
    STATUS_VALID = "VALID"
    STATUS_ERROR = "ERROR"
    STATUS_DUPLICATE = "DUPLICATE"

    STATUS_CHOICES = [
        (STATUS_VALID, "Valid"),
        (STATUS_ERROR, "Error"),
        (STATUS_DUPLICATE, "Duplicate"),
    ]

    batch = models.ForeignKey(
        SellerUploadBatch,
        on_delete=models.CASCADE,
        related_name="rows",
    )

    row_number = models.PositiveIntegerField(default=0)

    seller_order_code = models.CharField(max_length=100, blank=True, default="")
    receiver_name = models.CharField(max_length=200, blank=True, default="")
    receiver_phone = models.CharField(max_length=50, blank=True, default="")
    receiver_address = models.TextField(blank=True, default="")

    product_desc = models.CharField(max_length=255, blank=True, default="")
    quantity = models.PositiveIntegerField(default=1)

    cod = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    remark = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_VALID,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")

    imported_order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seller_upload_rows",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "row_number"],
                name="unique_seller_upload_batch_row_number",
            ),
        ]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["seller_order_code"]),
        ]

    def __str__(self):
        return f"{self.batch.code} row {self.row_number} - {self.status}"
