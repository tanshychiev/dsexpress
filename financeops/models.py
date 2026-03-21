from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone

from masterdata.models import Shipper


class StaffSalary(models.Model):
    ROLE_SHIPPER = "SHIPPER"
    ROLE_CALLCENTER = "CALLCENTER"

    ROLE_CHOICES = [
        (ROLE_SHIPPER, "SHIPPER"),
        (ROLE_CALLCENTER, "CALLCENTER"),
    ]

    shipper = models.ForeignKey(
        Shipper,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="salary_rows",
    )
    staff_name = models.CharField(max_length=120, blank=True, default="")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    monthly_salary_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["role", "staff_name", "id"]

    def __str__(self):
        if self.role == self.ROLE_SHIPPER and self.shipper:
            return f"{self.shipper.name} - ${self.monthly_salary_usd}"
        return f"{self.staff_name or '-'} - ${self.monthly_salary_usd}"


class MonthlyExpenseSetting(models.Model):
    month = models.DateField(unique=True)
    electricity_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    note = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month"]

    def __str__(self):
        return self.month.strftime("%Y-%m")


class ProvinceExpense(models.Model):
    expense_date = models.DateField(default=timezone.localdate)
    amount_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    note = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-expense_date", "-id"]

    def __str__(self):
        return f"{self.expense_date} - ${self.amount_usd}"