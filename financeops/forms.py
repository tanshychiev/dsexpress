from django import forms

from .models import MonthlyExpenseSetting, ProvinceExpense, StaffSalary


class StaffSalaryForm(forms.ModelForm):
    class Meta:
        model = StaffSalary
        fields = ["role", "shipper", "staff_name", "monthly_salary_usd", "is_active", "note"]
        widgets = {
            "role": forms.Select(attrs={"class": "input"}),
            "shipper": forms.Select(attrs={"class": "input"}),
            "staff_name": forms.TextInput(attrs={"class": "input", "placeholder": "Call center name if not shipper"}),
            "monthly_salary_usd": forms.NumberInput(attrs={"class": "input", "step": "0.01", "placeholder": "0.00"}),
            "is_active": forms.CheckboxInput(attrs={"class": "big-check"}),
            "note": forms.TextInput(attrs={"class": "input", "placeholder": "Note..."}),
        }


class MonthlyExpenseSettingForm(forms.ModelForm):
    class Meta:
        model = MonthlyExpenseSetting
        fields = ["month", "electricity_usd", "note"]
        widgets = {
            "month": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "electricity_usd": forms.NumberInput(attrs={"class": "input", "step": "0.01", "placeholder": "0.00"}),
            "note": forms.TextInput(attrs={"class": "input", "placeholder": "Note..."}),
        }


class ProvinceExpenseForm(forms.ModelForm):
    class Meta:
        model = ProvinceExpense
        fields = ["expense_date", "amount_usd", "note"]
        widgets = {
            "expense_date": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "amount_usd": forms.NumberInput(attrs={"class": "input", "step": "0.01", "placeholder": "0.00"}),
            "note": forms.TextInput(attrs={"class": "input", "placeholder": "Note..."}),
        }