from django.conf import settings
from django.db import models


class ShopDailyReport(models.Model):
    STATUS_WAITING_CHECK = "WAITING_CHECK"
    STATUS_APPROVED = "APPROVED"
    STATUS_NEED_FIX = "NEED_FIX"
    STATUS_HOLD = "HOLD"
    STATUS_TRANSFERRED = "TRANSFERRED"
    STATUS_SENT_TO_CUSTOMER = "SENT_TO_CUSTOMER"

    STATUS_CHOICES = [
        (STATUS_WAITING_CHECK, "WAITING_CHECK"),
        (STATUS_APPROVED, "APPROVED"),
        (STATUS_NEED_FIX, "NEED_FIX"),
        (STATUS_HOLD, "HOLD"),
        (STATUS_TRANSFERRED, "TRANSFERRED"),
        (STATUS_SENT_TO_CUSTOMER, "SENT_TO_CUSTOMER"),
    ]

    report_code = models.CharField(max_length=50, unique=True, db_index=True)

    shop = models.ForeignKey(
        "masterdata.Seller",
        on_delete=models.PROTECT,
        related_name="daily_reports",
    )

    report_date = models.DateField(db_index=True)

    done_count = models.PositiveIntegerField(default=0)
    pending_count = models.PositiveIntegerField(default=0)

    total_cod = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    png_path = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_WAITING_CHECK,
        db_index=True,
    )

    telegram_chat_id = models.CharField(max_length=100, blank=True, default="")
    telegram_message_id = models.CharField(max_length=100, blank=True, default="")

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="approved_daily_reports",
    )
    approved_at = models.DateTimeField(blank=True, null=True)

    reaction_emoji = models.CharField(max_length=20, blank=True, default="")
    telegram_actor_id = models.CharField(max_length=100, blank=True, default="")
    telegram_actor_name = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-report_date", "shop__name", "-id"]
        unique_together = [("shop", "report_date")]

    def __str__(self):
        return f"{self.report_code} - {self.shop.name} - {self.report_date}"


class ShopDailyReportStatusLog(models.Model):
    report = models.ForeignKey(
        ShopDailyReport,
        on_delete=models.CASCADE,
        related_name="status_logs",
    )
    old_status = models.CharField(max_length=30, blank=True, default="")
    new_status = models.CharField(max_length=30)
    emoji = models.CharField(max_length=20, blank=True, default="")
    actor_name = models.CharField(max_length=255, blank=True, default="")
    actor_telegram_id = models.CharField(max_length=100, blank=True, default="")
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.report.report_code}: {self.old_status} -> {self.new_status}"