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
        default=STATUS_PENDING
    )

    telegram_chat_id = models.CharField(max_length=100, blank=True, null=True)
    telegram_message_id = models.CharField(max_length=100, blank=True, null=True)

    processed_by_telegram_name = models.CharField(max_length=255, blank=True, null=True)
    processed_by_telegram_id = models.CharField(max_length=100, blank=True, null=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Booking {self.id} - {self.status}"


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

    ip_address = models.CharField(max_length=100, blank=True, default="")
    user_agent = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-login_at"]

    def __str__(self):
        return f"{self.seller.name} | {self.login_at:%Y-%m-%d %H:%M:%S}"

    @property
    def duration_minutes(self):
        end_time = self.logout_at or self.last_activity_at or timezone.now()
        delta = end_time - self.login_at
        return max(int(delta.total_seconds() // 60), 0)