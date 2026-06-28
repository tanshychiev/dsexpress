from django.conf import settings
from django.db import models


class Account(models.Model):
    ACCOUNT_TYPE_STAFF = "staff"
    ACCOUNT_TYPE_SELLER = "seller"
    ACCOUNT_TYPE_SHIPPER = "shipper"

    ACCOUNT_TYPE_CHOICES = [
        (ACCOUNT_TYPE_STAFF, "Staff"),
        (ACCOUNT_TYPE_SELLER, "Seller"),
        (ACCOUNT_TYPE_SHIPPER, "Shipper"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account",
    )

    account_type = models.CharField(
        max_length=20,
        choices=ACCOUNT_TYPE_CHOICES,
        default=ACCOUNT_TYPE_STAFF,
    )

    seller = models.ForeignKey(
        "masterdata.Seller",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="account_rows",
    )

    shipper = models.ForeignKey(
        "masterdata.Shipper",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="account_rows",
    )

    # Seller-specific role. It is intentionally separate from Django Group,
    # because every shop owns its own roles and permissions.
    seller_role = models.ForeignKey(
        "customerportal.SellerPortalRole",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounts",
    )

    # True only for Seller.portal_user. The owner always has full access.
    is_seller_owner = models.BooleanField(default=False)

    # Soft-delete fields. Archived accounts keep their history and audit trail.
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["seller", "account_type", "is_archived"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.get_account_type_display()}"

    def save(self, *args, **kwargs):
        if self.account_type == self.ACCOUNT_TYPE_STAFF:
            self.seller = None
            self.shipper = None
            self.seller_role = None
            self.is_seller_owner = False

        elif self.account_type == self.ACCOUNT_TYPE_SELLER:
            self.shipper = None
            if self.seller_id is None:
                self.seller_role = None
                self.is_seller_owner = False

        elif self.account_type == self.ACCOUNT_TYPE_SHIPPER:
            self.seller = None
            self.seller_role = None
            self.is_seller_owner = False

        super().save(*args, **kwargs)
