from __future__ import annotations

from calendar import monthrange
from datetime import date

from django.core.exceptions import FieldDoesNotExist
from django.db.models import DateField, DateTimeField
from django.utils import timezone

from deliverpp.models import PPDeliveryItem
from masterdata.models import Seller
from orders.models import Order
from provinceops.models import ProvinceBatch, ProvinceBatchItem

from .profit_dashboard_services import (
    _get_obj_date,
    _is_normal_pp_item,
    _is_return_order,
)


DEFAULT_MONTHLY_GOAL = 6000
DEFAULT_EXPECTED_GROWTH_RATE = 20
DEFAULT_MONTHS = 12


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _get_date_value(value):
    if not value:
        return None

    try:
        if hasattr(value, "date"):
            if timezone.is_aware(value):
                return timezone.localtime(value).date()
            return value.date()

        return value
    except Exception:
        return None


def _has_field(model, field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except FieldDoesNotExist:
        return False


def _find_date_field(model, candidates):
    for name in candidates:
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            continue

        if isinstance(field, (DateTimeField, DateField)):
            return name

    return None


def _date_lookup(model, field_name: str, op: str) -> str:
    field = model._meta.get_field(field_name)

    if isinstance(field, DateTimeField):
        return f"{field_name}__date__{op}"

    return f"{field_name}__{op}"


def _percent(part, total):
    if not total:
        return 0

    return round((part / total) * 100, 1)


def _growth(current, previous):
    if previous is None:
        return {
            "value": None,
            "text": "Base",
            "status": "same",
        }

    if previous == 0:
        if current > 0:
            return {
                "value": 100,
                "text": "+100%",
                "status": "up",
            }

        return {
            "value": 0,
            "text": "0%",
            "status": "same",
        }

    value = round(((current - previous) / previous) * 100, 1)

    if value > 0:
        return {
            "value": value,
            "text": f"+{value}%",
            "status": "up",
        }

    if value < 0:
        return {
            "value": value,
            "text": f"{value}%",
            "status": "down",
        }

    return {
        "value": 0,
        "text": "0%",
        "status": "same",
    }


def _get_seller_created_date(seller, seller_created_field: str | None):
    if not seller:
        return None

    if seller_created_field:
        value = getattr(seller, seller_created_field, None)
        created_date = _get_date_value(value)
        if created_date:
            return created_date

    portal_user = getattr(seller, "portal_user", None)
    if portal_user:
        for field in ["date_joined", "created_at", "created_on", "created"]:
            value = getattr(portal_user, field, None)
            created_date = _get_date_value(value)
            if created_date:
                return created_date

    return None


def _get_shop_name(order) -> str:
    seller = getattr(order, "seller", None)

    return (
        getattr(seller, "name", "")
        or getattr(order, "seller_name", "")
        or getattr(order, "shop_name", "")
        or "No Shop"
    )


def build_business_growth_tracker(
    as_of_date: date | None = None,
    months_count: int = DEFAULT_MONTHS,
    monthly_goal: int = DEFAULT_MONTHLY_GOAL,
    expected_growth_rate: float = DEFAULT_EXPECTED_GROWTH_RATE,
):
    today = as_of_date or timezone.localdate()

    months_count = max(3, min(int(months_count or DEFAULT_MONTHS), 36))
    monthly_goal = max(int(monthly_goal or 0), 0)
    expected_growth_rate = float(expected_growth_rate or 0)

    this_month = today.replace(day=1)
    start_month = _add_months(this_month, -(months_count - 1))
    end_exclusive = _add_months(this_month, 1)

    months = []
    month_map = {}

    cur = start_month
    for _ in range(months_count):
        next_month = _add_months(cur, 1)

        box = {
            "start": cur,
            "end": next_month,
            "month": cur.strftime("%b %Y"),
            "month_key": cur.strftime("%Y-%m"),
            "month_short": cur.strftime("%b"),

            "done_pp": 0,
            "done_province": 0,
            "done_all": 0,

            "new_customer_count": 0,
            "new_customer_pc": 0,
            "old_customer_pc": 0,
            "total_sent_pc": 0,
        }

        months.append(box)
        month_map[cur] = box
        cur = next_month

    customer_month_columns = [
        {
            "key": m["month_key"],
            "label": m["month_short"],
            "full": m["month"],
        }
        for m in months
    ]

    shop_compare_map = {}

    seller_created_field = _find_date_field(
        Seller,
        ["created_at", "created_on", "date_joined", "created"],
    )

    # =========================
    # NEW CUSTOMER CREATED PER MONTH
    # =========================
    seller_qs = Seller.objects.all()

    if _has_field(Seller, "portal_user"):
        seller_qs = seller_qs.select_related("portal_user")

    for seller in seller_qs:
        seller_created_date = _get_seller_created_date(
            seller,
            seller_created_field,
        )

        if not seller_created_date:
            continue

        if start_month <= seller_created_date < end_exclusive:
            seller_month = _month_start(seller_created_date)
            box = month_map.get(seller_month)

            if box:
                box["new_customer_count"] += 1

    # =========================
    # SENT PC BY NEW CUSTOMER VS OLD CUSTOMER
    # =========================
    order_date_field = _find_date_field(
        Order,
        ["created_at", "created_on", "date_created", "created"],
    )

    if order_date_field:
        order_lookup_gte = _date_lookup(Order, order_date_field, "gte")
        order_lookup_lt = _date_lookup(Order, order_date_field, "lt")

        order_qs = (
            Order.objects
            .select_related("seller")
            .filter(
                **{
                    order_lookup_gte: start_month,
                    order_lookup_lt: end_exclusive,
                }
            )
            .order_by("id")
        )

        for order in order_qs:
            if _is_return_order(order):
                continue

            order_date = _get_date_value(getattr(order, order_date_field, None))
            if not order_date:
                continue

            order_month = _month_start(order_date)
            box = month_map.get(order_month)

            if not box:
                continue

            seller = getattr(order, "seller", None)
            seller_id = getattr(seller, "id", None)
            shop_name = _get_shop_name(order)

            seller_created_date = _get_seller_created_date(
                seller,
                seller_created_field,
            )

            is_new_customer_month = (
                seller_created_date
                and box["start"] <= seller_created_date < box["end"]
            )

            if is_new_customer_month:
                box["new_customer_pc"] += 1
            else:
                box["old_customer_pc"] += 1

            shop_key = f"seller:{seller_id}" if seller_id else f"name:{shop_name}"

            if shop_key not in shop_compare_map:
                created_month_key = (
                    seller_created_date.strftime("%Y-%m")
                    if seller_created_date
                    else ""
                )

                created_month_label = (
                    seller_created_date.strftime("%b %Y")
                    if seller_created_date
                    else "-"
                )

                shop_compare_map[shop_key] = {
                    "shop_name": shop_name,
                    "created_month_key": created_month_key,
                    "created_month_label": created_month_label,
                    "total_sent": 0,
                    "total_new_pc": 0,
                    "total_old_pc": 0,
                    "monthly": {
                        m["month_key"]: {
                            "sent": 0,
                            "new_pc": 0,
                            "old_pc": 0,
                        }
                        for m in months
                    },
                }

            shop_box = shop_compare_map[shop_key]
            month_key = box["month_key"]

            shop_box["total_sent"] += 1
            shop_box["monthly"][month_key]["sent"] += 1

            if is_new_customer_month:
                shop_box["total_new_pc"] += 1
                shop_box["monthly"][month_key]["new_pc"] += 1
            else:
                shop_box["total_old_pc"] += 1
                shop_box["monthly"][month_key]["old_pc"] += 1

    # =========================
    # DONE PP
    # =========================
    pp_items = (
        PPDeliveryItem.objects
        .select_related("batch", "order", "order__seller")
        .filter(ticked=True)
        .order_by("id")
    )

    for item in pp_items:
        if not _is_normal_pp_item(item):
            continue

        order = getattr(item, "order", None)

        if _is_return_order(order):
            continue

        done_date = _get_obj_date(item, [
            "ticked_at",
            "done_at",
            "delivered_at",
            "completed_at",
            "updated_at",
            "created_at",
        ])

        if not done_date:
            batch = getattr(item, "batch", None)
            done_date = _get_obj_date(batch, [
                "done_at",
                "completed_at",
                "delivered_at",
                "updated_at",
                "created_at",
                "assigned_at",
            ])

        if not done_date:
            continue

        if start_month <= done_date < end_exclusive:
            box = month_map.get(_month_start(done_date))
            if box:
                box["done_pp"] += 1

    # =========================
    # DONE PROVINCE
    # =========================
    province_items = (
        ProvinceBatchItem.objects
        .select_related("batch", "order", "order__seller")
        .filter(batch__status=ProvinceBatch.STATUS_DONE)
        .order_by("id")
    )

    for item in province_items:
        order = getattr(item, "order", None)
        batch = getattr(item, "batch", None)

        if not batch or _is_return_order(order):
            continue

        done_date = _get_obj_date(batch, [
            "done_at",
            "completed_at",
            "delivered_at",
            "updated_at",
            "created_at",
            "assigned_at",
        ])

        if not done_date:
            done_date = _get_obj_date(item, [
                "done_at",
                "completed_at",
                "delivered_at",
                "updated_at",
                "created_at",
            ])

        if not done_date:
            continue

        if start_month <= done_date < end_exclusive:
            box = month_map.get(_month_start(done_date))
            if box:
                box["done_province"] += 1

    # =========================
    # CALCULATE MONTHLY RESULT
    # =========================
    previous_done = None
    previous_new_customer_count = None
    previous_new_customer_pc = None
    previous_total_sent_pc = None

    for m in months:
        m["done_all"] = m["done_pp"] + m["done_province"]
        m["total_sent_pc"] = m["new_customer_pc"] + m["old_customer_pc"]

        actual_growth = _growth(m["done_all"], previous_done)

        expected_done_by_growth = 0
        growth_target_percent = 0
        growth_target_gap = 0
        expected_growth_status = "same"
        expected_growth_text = "No Previous Data"

        if previous_done is not None:
            expected_done_by_growth = round(
                previous_done * (1 + (expected_growth_rate / 100))
            )

            growth_target_percent = _percent(
                m["done_all"],
                expected_done_by_growth,
            )

            growth_target_gap = max(expected_done_by_growth - m["done_all"], 0)

            actual_value = actual_growth.get("value") or 0

            if actual_value >= expected_growth_rate:
                expected_growth_status = "up"
                expected_growth_text = "Over Target"
            else:
                expected_growth_status = "down"
                expected_growth_text = "Below Target"

        days_in_month = monthrange(m["start"].year, m["start"].month)[1]

        if m["start"].year == today.year and m["start"].month == today.month:
            passed_days = max(today.day, 1)
            expected_end_month = round(
                (m["done_all"] / passed_days) * days_in_month
            )
        else:
            expected_end_month = m["done_all"]

        goal_percent = _percent(m["done_all"], monthly_goal)
        gap_to_goal = max(monthly_goal - m["done_all"], 0)

        m["actual_growth"] = actual_growth
        m["expected_growth_rate"] = expected_growth_rate
        m["expected_done_by_growth"] = expected_done_by_growth
        m["growth_target_percent"] = growth_target_percent
        m["growth_target_gap"] = growth_target_gap
        m["expected_growth_status"] = expected_growth_status
        m["expected_growth_text"] = expected_growth_text

        m["monthly_goal"] = monthly_goal
        m["expected_end_month"] = expected_end_month
        m["goal_percent"] = goal_percent
        m["gap_to_goal"] = gap_to_goal

        m["new_customer_growth"] = _growth(
            m["new_customer_count"],
            previous_new_customer_count,
        )

        m["new_pc_growth"] = _growth(
            m["new_customer_pc"],
            previous_new_customer_pc,
        )

        m["total_sent_growth"] = _growth(
            m["total_sent_pc"],
            previous_total_sent_pc,
        )

        m["new_pc_percent"] = _percent(
            m["new_customer_pc"],
            m["total_sent_pc"],
        )

        m["old_pc_percent"] = _percent(
            m["old_customer_pc"],
            m["total_sent_pc"],
        )

        if m["new_customer_count"]:
            m["avg_pc_per_new_customer"] = round(
                m["new_customer_pc"] / m["new_customer_count"],
                1,
            )
        else:
            m["avg_pc_per_new_customer"] = 0

        previous_done = m["done_all"]
        previous_new_customer_count = m["new_customer_count"]
        previous_new_customer_pc = m["new_customer_pc"]
        previous_total_sent_pc = m["total_sent_pc"]

    # =========================
    # CUSTOMER SHOP MONTHLY COMPARE
    # =========================
    customer_shop_rows = []

    for shop in shop_compare_map.values():
        cells = []
        active_months = 0
        best_month = "-"
        best_month_pc = 0

        for col in customer_month_columns:
            month_key = col["key"]
            cell = shop["monthly"].get(month_key, {
                "sent": 0,
                "new_pc": 0,
                "old_pc": 0,
            })

            sent = cell["sent"]

            if sent > 0:
                active_months += 1

            if sent > best_month_pc:
                best_month_pc = sent
                best_month = col["full"]

            cells.append({
                "month_key": month_key,
                "label": col["label"],
                "sent": sent,
                "new_pc": cell["new_pc"],
                "old_pc": cell["old_pc"],
                "is_new_month": shop["created_month_key"] == month_key,
            })

        avg_per_active_month = 0
        if active_months > 0:
            avg_per_active_month = round(shop["total_sent"] / active_months, 1)

        customer_shop_rows.append({
            "shop_name": shop["shop_name"],
            "created_month_label": shop["created_month_label"],
            "total_sent": shop["total_sent"],
            "total_new_pc": shop["total_new_pc"],
            "total_old_pc": shop["total_old_pc"],
            "new_pc_percent": _percent(shop["total_new_pc"], shop["total_sent"]),
            "old_pc_percent": _percent(shop["total_old_pc"], shop["total_sent"]),
            "active_months": active_months,
            "avg_per_active_month": avg_per_active_month,
            "best_month": best_month,
            "best_month_pc": best_month_pc,
            "cells": cells,
        })

    customer_shop_rows.sort(
        key=lambda x: (
            -x["total_sent"],
            x["shop_name"].lower(),
        )
    )

    this_month_data = months[-1] if months else None

    return {
        "months": months,
        "this_month": this_month_data,

        "total_done_all": sum(m["done_all"] for m in months),
        "total_done_pp": sum(m["done_pp"] for m in months),
        "total_done_province": sum(m["done_province"] for m in months),

        "total_new_customers": sum(m["new_customer_count"] for m in months),
        "total_new_customer_pc": sum(m["new_customer_pc"] for m in months),
        "total_old_customer_pc": sum(m["old_customer_pc"] for m in months),
        "total_sent_pc": sum(m["total_sent_pc"] for m in months),

        "customer_month_columns": customer_month_columns,
        "customer_shop_rows": customer_shop_rows,
        "customer_shop_count": len(customer_shop_rows),

        "chart_labels": [m["month"] for m in months],
        "chart_done_all": [m["done_all"] for m in months],
        "chart_done_pp": [m["done_pp"] for m in months],
        "chart_done_province": [m["done_province"] for m in months],
        "chart_goal": [m["monthly_goal"] for m in months],
        "chart_expected_end_month": [m["expected_end_month"] for m in months],
        "chart_expected_done_by_growth": [
            m["expected_done_by_growth"] for m in months
        ],
        "chart_new_customer_pc": [m["new_customer_pc"] for m in months],
        "chart_old_customer_pc": [m["old_customer_pc"] for m in months],
        "chart_new_customers": [m["new_customer_count"] for m in months],
    }