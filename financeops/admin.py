from django.contrib import admin

from .models import MonthlyExpenseSetting, ProvinceExpense, StaffSalary


@admin.register(StaffSalary)
class StaffSalaryAdmin(admin.ModelAdmin):
    list_display = ("id", "role", "shipper", "staff_name", "monthly_salary_usd", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("staff_name", "shipper__name", "note")


@admin.register(MonthlyExpenseSetting)
class MonthlyExpenseSettingAdmin(admin.ModelAdmin):
    list_display = ("id", "month", "electricity_usd", "note")
    search_fields = ("note",)


@admin.register(ProvinceExpense)
class ProvinceExpenseAdmin(admin.ModelAdmin):
    list_display = ("id", "expense_date", "amount_usd", "note")
    list_filter = ("expense_date",)
    search_fields = ("note",)