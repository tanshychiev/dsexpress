from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import MonthlyExpenseSettingForm, ProvinceExpenseForm, StaffSalaryForm
from .models import MonthlyExpenseSetting, ProvinceExpense, StaffSalary


@login_required
def finance_home(request):
    staff_count = StaffSalary.objects.count()
    active_staff_count = StaffSalary.objects.filter(is_active=True).count()
    month_count = MonthlyExpenseSetting.objects.count()
    province_count = ProvinceExpense.objects.count()

    latest_month = MonthlyExpenseSetting.objects.order_by("-month").first()
    latest_province = ProvinceExpense.objects.order_by("-expense_date", "-id").first()

    return render(
        request,
        "financeops/finance_home.html",
        {
            "staff_count": staff_count,
            "active_staff_count": active_staff_count,
            "month_count": month_count,
            "province_count": province_count,
            "latest_month": latest_month,
            "latest_province": latest_province,
        },
    )


@login_required
def staff_salary_list(request):
    edit_id = request.GET.get("edit")
    edit_obj = None

    if edit_id and str(edit_id).isdigit():
        edit_obj = StaffSalary.objects.filter(pk=int(edit_id)).first()

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save":
            row_id = (request.POST.get("row_id") or "").strip()
            instance = StaffSalary.objects.filter(pk=int(row_id)).first() if row_id.isdigit() else None
            form = StaffSalaryForm(request.POST, instance=instance)

            if form.is_valid():
                obj = form.save(commit=False)

                if obj.role == StaffSalary.ROLE_SHIPPER:
                    if not obj.shipper:
                        messages.error(request, "Please select shipper for SHIPPER role.")
                        return redirect("staff_salary_list")
                    obj.staff_name = ""
                else:
                    obj.shipper = None
                    if not (obj.staff_name or "").strip():
                        messages.error(request, "Please fill staff name for CALLCENTER role.")
                        return redirect("staff_salary_list")

                obj.save()
                messages.success(request, "Salary saved.")
                return redirect("staff_salary_list")

            messages.error(request, "Please check the form.")
        elif action == "delete":
            row_id = (request.POST.get("row_id") or "").strip()
            if row_id.isdigit():
                obj = StaffSalary.objects.filter(pk=int(row_id)).first()
                if obj:
                    obj.delete()
                    messages.success(request, "Salary deleted.")
            return redirect("staff_salary_list")

    form = StaffSalaryForm(instance=edit_obj)
    rows = StaffSalary.objects.select_related("shipper").order_by("role", "staff_name", "id")

    return render(
        request,
        "financeops/staff_salary_list.html",
        {
            "form": form,
            "edit_obj": edit_obj,
            "rows": rows,
        },
    )


@login_required
def monthly_expense_list(request):
    edit_id = request.GET.get("edit")
    edit_obj = None

    if edit_id and str(edit_id).isdigit():
        edit_obj = MonthlyExpenseSetting.objects.filter(pk=int(edit_id)).first()

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save":
            row_id = (request.POST.get("row_id") or "").strip()
            instance = MonthlyExpenseSetting.objects.filter(pk=int(row_id)).first() if row_id.isdigit() else None
            form = MonthlyExpenseSettingForm(request.POST, instance=instance)

            if form.is_valid():
                obj = form.save(commit=False)

                clash = MonthlyExpenseSetting.objects.filter(month=obj.month)
                if obj.pk:
                    clash = clash.exclude(pk=obj.pk)

                if clash.exists():
                    messages.error(request, "This month already exists. Please edit the existing row.")
                    return redirect("monthly_expense_list")

                obj.save()
                messages.success(request, "Monthly electricity saved.")
                return redirect("monthly_expense_list")

            messages.error(request, "Please check the form.")
        elif action == "delete":
            row_id = (request.POST.get("row_id") or "").strip()
            if row_id.isdigit():
                obj = MonthlyExpenseSetting.objects.filter(pk=int(row_id)).first()
                if obj:
                    obj.delete()
                    messages.success(request, "Monthly row deleted.")
            return redirect("monthly_expense_list")

    form = MonthlyExpenseSettingForm(instance=edit_obj)
    rows = MonthlyExpenseSetting.objects.order_by("-month")

    return render(
        request,
        "financeops/monthly_expense_list.html",
        {
            "form": form,
            "edit_obj": edit_obj,
            "rows": rows,
        },
    )


@login_required
def province_expense_list(request):
    edit_id = request.GET.get("edit")
    edit_obj = None

    if edit_id and str(edit_id).isdigit():
        edit_obj = ProvinceExpense.objects.filter(pk=int(edit_id)).first()

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save":
            row_id = (request.POST.get("row_id") or "").strip()
            instance = ProvinceExpense.objects.filter(pk=int(row_id)).first() if row_id.isdigit() else None
            form = ProvinceExpenseForm(request.POST, instance=instance)

            if form.is_valid():
                form.save()
                messages.success(request, "Province expense saved.")
                return redirect("province_expense_list")

            messages.error(request, "Please check the form.")
        elif action == "delete":
            row_id = (request.POST.get("row_id") or "").strip()
            if row_id.isdigit():
                obj = ProvinceExpense.objects.filter(pk=int(row_id)).first()
                if obj:
                    obj.delete()
                    messages.success(request, "Province expense deleted.")
            return redirect("province_expense_list")

    form = ProvinceExpenseForm(instance=edit_obj)
    rows = ProvinceExpense.objects.order_by("-expense_date", "-id")

    return render(
        request,
        "financeops/province_expense_list.html",
        {
            "form": form,
            "edit_obj": edit_obj,
            "rows": rows,
        },
    )