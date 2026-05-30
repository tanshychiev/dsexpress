from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from masterdata.models import Seller


class InventorySellerSetting(models.Model):
    STRICT = "STRICT"
    OPTIONAL = "OPTIONAL"
    NO_STOCK = "NO_STOCK"

    STOCK_MODE_CHOICES = [
        (STRICT, "Strict Stock"),
        (OPTIONAL, "Optional Stock"),
        (NO_STOCK, "No Stock / Shop Prepare"),
    ]

    seller = models.OneToOneField(
        Seller,
        on_delete=models.CASCADE,
        related_name="inventory_setting",
    )

    stock_mode = models.CharField(
        max_length=20,
        choices=STOCK_MODE_CHOICES,
        default=OPTIONAL,
        db_index=True,
    )

    show_stock_in_portal = models.BooleanField(default=True)
    note = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Inventory Seller Setting"
        verbose_name_plural = "Inventory Seller Settings"

    def __str__(self):
        return f"{self.seller} - {self.stock_mode}"


class StockProduct(models.Model):
    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="stock_products",
    )

    name = models.CharField(max_length=255)

    product_type = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="Example: Serum, Cream, Gel, Shirt",
    )

    sku = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_index=True,
    )

    photo = models.ImageField(
        upload_to="stock_products/",
        blank=True,
        null=True,
    )

    location = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Example: Shelf A1",
    )

    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_inventory_products",
    )

    class Meta:
        ordering = ["seller__name", "name"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if not self.sku:
            seller_code = (
                getattr(self.seller, "code", "")
                or getattr(self.seller, "name", "")
                or "SHOP"
            )
            seller_code = "".join(
                ch for ch in seller_code.upper() if ch.isalnum()
            )[:8] or "SHOP"

            type_code = "".join(
                ch for ch in (self.product_type or "ITEM").upper()
                if ch.isalnum()
            )[:3] or "ITM"

            StockProduct.objects.filter(pk=self.pk, sku="").update(
                sku=f"{seller_code}-{type_code}-{self.pk:03d}"
            )

    def __str__(self):
        return f"{self.seller} - {self.name}"


class StockAlias(models.Model):
    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="stock_aliases",
    )

    product = models.ForeignKey(
        StockProduct,
        on_delete=models.CASCADE,
        related_name="aliases",
    )

    alias_text = models.CharField(max_length=255, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    class Meta:
        unique_together = [("seller", "alias_text")]
        ordering = ["seller__name", "alias_text"]

    def __str__(self):
        return f"{self.alias_text} → {self.product.name}"


class StockSnapshot(models.Model):
    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="stock_snapshots",
    )

    product = models.ForeignKey(
        StockProduct,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )

    confirmed_qty = models.IntegerField(default=0)

    confirmed_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-confirmed_at", "-id"]

    def __str__(self):
        return f"{self.product.name}: {self.confirmed_qty} @ {self.confirmed_at:%Y-%m-%d}"


class StockMovement(models.Model):
    STOCK_IN = "STOCK_IN"
    ORDER_RESERVED = "ORDER_RESERVED"
    ORDER_RELEASED = "ORDER_RELEASED"
    ORDER_DELIVERED = "ORDER_DELIVERED"
    RETURN_GOOD = "RETURN_GOOD"
    RETURN_DAMAGED = "RETURN_DAMAGED"
    ADJUSTMENT = "ADJUSTMENT"
    IMPORT_UNMATCHED = "IMPORT_UNMATCHED"
    PRODUCT_CHANGED = "PRODUCT_CHANGED"
    STOCK_LACK = "STOCK_LACK"
    CONFIRM = "CONFIRM"

    TYPE_CHOICES = [
        (STOCK_IN, "Stock In"),
        (ORDER_RESERVED, "Order Reserved"),
        (ORDER_RELEASED, "Order Released"),
        (ORDER_DELIVERED, "Order Delivered"),
        (RETURN_GOOD, "Return Good"),
        (RETURN_DAMAGED, "Return Damaged"),
        (ADJUSTMENT, "Adjustment"),
        (IMPORT_UNMATCHED, "Import Unmatched"),
        (PRODUCT_CHANGED, "Product Changed"),
        (STOCK_LACK, "Stock Lack"),
        (CONFIRM, "Confirm Stock"),
    ]

    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="stock_movements",
    )

    product = models.ForeignKey(
        StockProduct,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="movements",
    )

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="stock_movements",
    )

    movement_type = models.CharField(
        max_length=40,
        choices=TYPE_CHOICES,
        db_index=True,
    )

    # Positive = stock increase, negative = stock decrease.
    qty_delta = models.IntegerField(default=0)

    note = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.movement_type} {self.qty_delta} {self.product or '-'}"


class OrderStockLink(models.Model):
    LINKED = "LINKED"
    UNMATCHED = "UNMATCHED"
    STOCK_LACK = "STOCK_LACK"
    NO_STOCK_REQUIRED = "NO_STOCK_REQUIRED"

    STATUS_CHOICES = [
        (LINKED, "Linked"),
        (UNMATCHED, "Unmatched"),
        (STOCK_LACK, "Stock Lack"),
        (NO_STOCK_REQUIRED, "No Stock Required"),
    ]

    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="stock_link",
    )

    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="order_stock_links",
    )

    product = models.ForeignKey(
        StockProduct,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="order_links",
    )

    raw_product_text = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    quantity = models.IntegerField(default=1)

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=UNMATCHED,
        db_index=True,
    )

    shortage_qty = models.IntegerField(default=0)
    reserved_qty = models.IntegerField(default=0)

    delivered_at = models.DateTimeField(blank=True, null=True)
    released_at = models.DateTimeField(blank=True, null=True)
    returned_at = models.DateTimeField(blank=True, null=True)

    note = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="updated_inventory_order_links",
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.order} - {self.status}"


class OrderStockItem(models.Model):
    LINKED = "LINKED"
    STOCK_LACK = "STOCK_LACK"

    STATUS_CHOICES = [
        (LINKED, "Linked"),
        (STOCK_LACK, "Stock Lack"),
    ]

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="stock_items",
    )

    link = models.ForeignKey(
        OrderStockLink,
        on_delete=models.CASCADE,
        related_name="items",
        blank=True,
        null=True,
    )

    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name="order_stock_items",
    )

    product = models.ForeignKey(
        StockProduct,
        on_delete=models.PROTECT,
        related_name="order_stock_items",
    )

    quantity = models.IntegerField(default=1)
    reserved_qty = models.IntegerField(default=0)
    shortage_qty = models.IntegerField(default=0)

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=LINKED,
        db_index=True,
    )

    raw_product_text = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order} - {self.product.name} x {self.quantity}"