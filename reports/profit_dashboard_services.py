from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from deliverpp.models import PPDeliveryItem
from financeops.models import MonthlyExpenseSetting, ProvinceExpense, StaffSalary


ZERO = Decimal("0.00")


def _to_decimal(v):
    try:
        if v is None:
            return ZERO
        return Decimal(str(v))
    except Exception:
        return ZERO


def _daterange(start_date: date, end_date: date):
    out = []
    cur = start_date
    while cur <= end_date:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _build_daily_fee_income(date_from: date, date_to: date):
    """
    Income source:
    - order.delivery_fee
    - order.additional_fee
    Count by delivery cleared date
    Only ticked rows
    """
    qs = (
        PPDeliveryItem.objects
        .select_related("order")
        .filter(
            ticked=True,
            delivery_cleared_at__date__gte=date_from,
            delivery_cleared_at__date__lte=date_to,
        )
        .order_by("delivery_cleared_at", "id")
    )

    out = defaultdict(lambda: ZERO)

    for item in qs:
        done_at = getattr(item, "delivery_cleared_at", None)
        if not done_at:
            continue

        day = done_at.date()
        order = getattr(item, "order", None)
        if not order:
            continue

        delivery_fee = _to_decimal(getattr(order, "delivery_fee", 0))
        additional_fee = _to_decimal(getattr(order, "additional_fee", 0))

        out[day] += delivery_fee + additional_fee

    return out


def _build_daily_commission(date_from: date, date_to: date):
    """
    Commission is per shipper per day:
    done pc > 10 => (done - 10) * 1500 riel
    Convert KHR -> USD by fixed 4100
    """
    qs = (
        PPDeliveryItem.objects
        .select_related("batch", "batch__shipper")
        .filter(
            ticked=True,
            delivery_cleared_at__date__gte=date_from,
            delivery_cleared_at__date__lte=date_to,
        )
        .order_by("delivery_cleared_at", "id")
    )

    done_map = defaultdict(int)

    for item in qs:
        done_at = getattr(item, "delivery_cleared_at", None)
        batch = getattr(item, "batch", None)
        shipper = getattr(batch, "shipper", None) if batch else None
        shipper_name = getattr(shipper, "name", "") or "-"

        if not done_at:
            continue

        day = done_at.date()
        done_map[(shipper_name, day)] += 1

    daily_commission_usd = defaultdict(lambda: ZERO)

    for (_shipper_name, day), done_count in done_map.items():
        commission_pc = max(done_count - 10, 0)
        commission_khr = commission_pc * 1500
        commission_usd = Decimal(commission_khr) / Decimal("4100")
        daily_commission_usd[day] += commission_usd

    return daily_commission_usd


def _build_daily_province_expense(date_from: date, date_to: date):
    qs = (
        ProvinceExpense.objects
        .filter(
            expense_date__gte=date_from,
            expense_date__lte=date_to,
        )
        .order_by("expense_date", "id")
    )

    out = defaultdict(lambda: ZERO)

    for row in qs:
        out[row.expense_date] += _to_decimal(row.amount_usd)

    return out


def _build_daily_salary_share(date_from: date, date_to: date):
    """
    Monthly fixed salary prorated by calendar day.
    SHIPPER and CALLCENTER split separately.
    """
    all_days = _daterange(date_from, date_to)

    shipper_monthly_map = defaultdict(lambda: ZERO)
    callcenter_monthly_map = defaultdict(lambda: ZERO)

    salary_rows = StaffSalary.objects.filter(is_active=True).order_by("id")
    for row in salary_rows:
        monthly_salary = _to_decimal(row.monthly_salary_usd)
        role = (row.role or "").upper()

        if role == StaffSalary.ROLE_SHIPPER:
            # spread active shipper salaries equally across every day of each month
            pass
        elif role == StaffSalary.ROLE_CALLCENTER:
            pass

    # build month totals
    for row in salary_rows:
        month_salary = _to_decimal(row.monthly_salary_usd)
        role = (row.role or "").upper()

        # no specific month field on salary, so treat current salary as active for selected range months
        cur = date(date_from.year, date_from.month, 1)
        endm = date(date_to.year, date_to.month, 1)

        while cur <= endm:
            month_key = (cur.year, cur.month)
            if role == StaffSalary.ROLE_SHIPPER:
                shipper_monthly_map[month_key] += month_salary
            elif role == StaffSalary.ROLE_CALLCENTER:
                callcenter_monthly_map[month_key] += month_salary

            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)

    daily_shipper_salary = defaultdict(lambda: ZERO)
    daily_callcenter_salary = defaultdict(lambda: ZERO)

    for d in all_days:
        month_key = (d.year, d.month)
        dim = Decimal(_days_in_month(d))

        daily_shipper_salary[d] = shipper_monthly_map[month_key] / dim if dim else ZERO
        daily_callcenter_salary[d] = callcenter_monthly_map[month_key] / dim if dim else ZERO

    return daily_shipper_salary, daily_callcenter_salary


def _build_daily_electricity_share(date_from: date, date_to: date):
    all_days = _daterange(date_from, date_to)

    month_electric_map = defaultdict(lambda: ZERO)
    qs = MonthlyExpenseSetting.objects.all().order_by("month")

    for row in qs:
        month_key = (row.month.year, row.month.month)
        month_electric_map[month_key] += _to_decimal(row.electricity_usd)

    daily_electric = defaultdict(lambda: ZERO)
    for d in all_days:
        month_key = (d.year, d.month)
        dim = Decimal(_days_in_month(d))
        daily_electric[d] = month_electric_map[month_key] / dim if dim else ZERO

    return daily_electric


def build_profit_dashboard(date_from: date, date_to: date):
    all_days = _daterange(date_from, date_to)

    fee_map = _build_daily_fee_income(date_from, date_to)
    commission_map = _build_daily_commission(date_from, date_to)
    province_map = _build_daily_province_expense(date_from, date_to)
    shipper_salary_map, callcenter_salary_map = _build_daily_salary_share(date_from, date_to)
    electric_map = _build_daily_electricity_share(date_from, date_to)

    rows = []
    total = {
        "fee_income_usd": ZERO,
        "province_expense_usd": ZERO,
        "commission_usd": ZERO,
        "shipper_salary_usd": ZERO,
        "callcenter_salary_usd": ZERO,
        "electricity_usd": ZERO,
        "net_profit_usd": ZERO,
    }

    for d in all_days:
        fee_income = fee_map[d]
        province_expense = province_map[d]
        commission = commission_map[d]
        shipper_salary = shipper_salary_map[d]
        callcenter_salary = callcenter_salary_map[d]
        electricity = electric_map[d]

        net_profit = fee_income - province_expense - commission - shipper_salary - callcenter_salary - electricity

        row = {
            "date": d,
            "fee_income_usd": fee_income,
            "province_expense_usd": province_expense,
            "commission_usd": commission,
            "shipper_salary_usd": shipper_salary,
            "callcenter_salary_usd": callcenter_salary,
            "electricity_usd": electricity,
            "net_profit_usd": net_profit,
            "is_loss": net_profit < 0,
            "is_zero_day": fee_income == ZERO and province_expense == ZERO and commission == ZERO,
        }
        rows.append(row)

        total["fee_income_usd"] += fee_income
        total["province_expense_usd"] += province_expense
        total["commission_usd"] += commission
        total["shipper_salary_usd"] += shipper_salary
        total["callcenter_salary_usd"] += callcenter_salary
        total["electricity_usd"] += electricity
        total["net_profit_usd"] += net_profit

    return {
        "rows": rows,
        "total": total,
    }