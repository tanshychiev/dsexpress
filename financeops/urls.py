from django.urls import path

from .views import (
    finance_home,
    monthly_expense_list,
    province_expense_list,
    staff_salary_list,
)

urlpatterns = [
    path("", finance_home, name="finance_home"),
    path("staff-salary/", staff_salary_list, name="staff_salary_list"),
    path("monthly-expense/", monthly_expense_list, name="monthly_expense_list"),
    path("province-expense/", province_expense_list, name="province_expense_list"),
]