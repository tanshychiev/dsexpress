from __future__ import annotations

from django.conf import settings
from django.db import models


# =============================
# SELLER
# =============================
class Seller(models.Model):
    code = models.CharField(max_length=10, unique=True, blank=True)

    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    # 🔑 Portal login (seller portal)
    portal_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="seller_portal",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.code} - {self.name}"


# =============================
# SHIPPER
# =============================
class Shipper(models.Model):
    SHIPPER_TYPE_CHOICES = [
        ("DELIVERY", "Delivery"),
        ("PROVINCE", "Province"),
        ("RETURN", "Return"),
    ]

    code = models.CharField(max_length=10, unique=True, blank=True)

    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)

    shipper_type = models.CharField(
        max_length=20,
        choices=SHIPPER_TYPE_CHOICES,
        default="DELIVERY",
    )

    is_active = models.BooleanField(default=True)

    # 🔑 OPTIONAL (future: shipper login)
    portal_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shipper_portal",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.code} - {self.name}"