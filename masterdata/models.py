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


# =============================
# SELLER PRICE RULE
# =============================
class SellerPriceRule(models.Model):
    TYPE_CHOICES = [
        ("PV", "Province"),
        ("COD", "COD"),
    ]

    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="price_rules",
    )
    shipper = models.ForeignKey(
        Shipper,
        on_delete=models.CASCADE,
        related_name="seller_price_rules",
    )

    rule_type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
    )

    delivery_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    additional_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    percent_cod = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=0,
        help_text="0.03 = 3%",
    )

    is_locked = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("seller", "shipper", "rule_type")
        ordering = ["seller__name", "shipper__name", "rule_type"]

    def __str__(self):
        return f"{self.seller.name} - {self.shipper.name} - {self.rule_type}"