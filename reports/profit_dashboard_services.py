from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

from deliverpp.models import ClearPPCOD, PPDeliveryBatch, PPDeliveryItem
from orders.models import Order


ZERO = Decimal("0.00")
PP_SHIPPER_TYPE = "DELIVERY"
PROVINCE_SHIPPER_TYPE = "PROVINCE"


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


def _calc_done_percent(done_count, sent_count):
    if not sent_count:
        return 0
    return round((done_count / sent_count) * 100, 2)


def _is_normal_item(item) -> bool:
    """
    Dashboard rule:
    count only NORMAL done pc
    do NOT count RETURN done pc
    do NOT count RETURN batch
    """
    source_type = str(getattr(item, "source_type", "") or "").upper()
    if source_type:
        return source_type == str(getattr(PPDeliveryItem, "SOURCE_NORMAL", "NORMAL")).upper()

    order = getattr(item, "order", None)
    status = str(getattr(order, "status", "") or "").upper()
    if status in {"RETURN_ASSIGNED", "RETURNED", "DONE_RETURN"}:
        return False

    return True


def _build_today_cards(target_date: date):
    created_count = Order.objects.filter(created_at__date=target_date).count()

    sent_pp = 0
    sent_province = 0
    done_pp = 0
    done_province = 0

    batch_qs = (
        PPDeliveryBatch.objects
        .select_related("shipper")
        .prefetch_related("items")
        .filter(assigned_at__date=target_date)
        .order_by("assigned_at", "id")
    )

    for batch in batch_qs:
        shipper = getattr(batch, "shipper", None)
        shipper_type = getattr(shipper, "shipper_type", "") or ""

        prefetched_items = getattr(batch, "_prefetched_objects_cache", {}).get("items")
        items = list(prefetched_items) if prefetched_items is not None else list(batch.items.all())

        normal_count = sum(1 for item in items if _is_normal_item(item))

        if shipper_type == PROVINCE_SHIPPER_TYPE:
            sent_province += normal_count
        else:
            sent_pp += normal_count

    done_item_qs = (
        PPDeliveryItem.objects
        .select_related("batch", "batch__shipper", "order")
        .filter(
            ticked=True,
            batch__assigned_at__date=target_date,
            batch__assigned_at__isnull=False,
        )
        .order_by("batch__assigned_at", "id")
    )

    for item in done_item_qs:
        if not _is_normal_item(item):
            continue

        batch = getattr(item, "batch", None)
        shipper = getattr(batch, "shipper", None) if batch else None
        shipper_type = getattr(shipper, "shipper_type", "") or ""

        if shipper_type == PROVINCE_SHIPPER_TYPE:
            done_province += 1
        else:
            done_pp += 1

    order_fee_agg = Order.objects.filter(created_at__date=target_date).aggregate(
        delivery_fee_total=Sum("delivery_fee"),
        additional_fee_total=Sum("additional_fee"),
        province_fee_total=Sum("province_fee"),
    )

    delivery_fee_total = _to_decimal(order_fee_agg.get("delivery_fee_total"))
    additional_fee_total = _to_decimal(order_fee_agg.get("additional_fee_total"))
    province_fee_total = _to_decimal(order_fee_agg.get("province_fee_total"))
    expense_total = additional_fee_total + province_fee_total
    revenue_total = delivery_fee_total - expense_total

    cod_total = ZERO
    money_received = ZERO

    cod_qs = (
        ClearPPCOD.objects
        .select_related("batch", "batch__shipper")
        .filter(batch__assigned_at__date=target_date)
    )

    for row in cod_qs:
        cod_total += _to_decimal(getattr(row, "target_total_usd", 0))
        money_received += (
            _to_decimal(getattr(row, "cash_usd", 0))
            + _to_decimal(getattr(row, "aba_usd", 0))
        )

    total_sent = sent_pp + sent_province
    total_done = done_pp + done_province

    return {
        "created": created_count,
        "sent_pp": sent_pp,
        "sent_province": sent_province,
        "done_pp": done_pp,
        "done_province": done_province,
        "pending_pp": max(sent_pp - done_pp, 0),
        "pending_province": max(sent_province - done_province, 0),
        "total_sent": total_sent,
        "total_done": total_done,
        "overall_done_percent": _calc_done_percent(total_done, total_sent),
        "pp_done_percent": _calc_done_percent(done_pp, sent_pp),
        "province_done_percent": _calc_done_percent(done_province, sent_province),
        "shipment_fee_total": delivery_fee_total,
        "expense_total": expense_total,
        "revenue_total": revenue_total,
        "cod_total": cod_total,
        "money_received": money_received,
    }


def _build_trend_30_days(end_date: date):
    start_date = end_date - timedelta(days=29)
    days = _daterange(start_date, end_date)

    trend_map = {
        d: {
            "date": d.strftime("%Y-%m-%d"),
            "created": 0,
            "shipment_fee": 0.0,
            "expense": 0.0,
            "revenue": 0.0,
            "total_done": 0,
            "done_pp": 0,
            "done_province": 0,
        }
        for d in days
    }

    for d in days:
        agg = Order.objects.filter(created_at__date=d).aggregate(
            delivery_fee_total=Sum("delivery_fee"),
            additional_fee_total=Sum("additional_fee"),
            province_fee_total=Sum("province_fee"),
        )

        created_count = Order.objects.filter(created_at__date=d).count()
        delivery_fee_total = _to_decimal(agg.get("delivery_fee_total"))
        additional_fee_total = _to_decimal(agg.get("additional_fee_total"))
        province_fee_total = _to_decimal(agg.get("province_fee_total"))
        expense_total = additional_fee_total + province_fee_total
        revenue_total = delivery_fee_total - expense_total

        trend_map[d]["created"] = created_count
        trend_map[d]["shipment_fee"] = float(delivery_fee_total)
        trend_map[d]["expense"] = float(expense_total)
        trend_map[d]["revenue"] = float(revenue_total)

    done_item_qs = (
        PPDeliveryItem.objects
        .select_related("batch", "batch__shipper", "order")
        .filter(
            ticked=True,
            batch__assigned_at__date__gte=start_date,
            batch__assigned_at__date__lte=end_date,
            batch__assigned_at__isnull=False,
        )
        .order_by("batch__assigned_at", "id")
    )

    for item in done_item_qs:
        if not _is_normal_item(item):
            continue

        batch = getattr(item, "batch", None)
        assigned_at = getattr(batch, "assigned_at", None) if batch else None
        if not assigned_at:
            continue

        d = assigned_at.date()
        if d not in trend_map:
            continue

        shipper = getattr(batch, "shipper", None)
        shipper_type = getattr(shipper, "shipper_type", "") or ""

        if shipper_type == PROVINCE_SHIPPER_TYPE:
            trend_map[d]["done_province"] += 1
        else:
            trend_map[d]["done_pp"] += 1

    for d in days:
        trend_map[d]["total_done"] = trend_map[d]["done_pp"] + trend_map[d]["done_province"]

    return [trend_map[d] for d in days]


def _build_shipper_summary(date_from: date, date_to: date):
    grouped = defaultdict(lambda: {
        "shipper_name": "-",
        "done_orders": 0,
        "cod_total": ZERO,
        "received": ZERO,
    })

    cod_qs = (
        ClearPPCOD.objects
        .select_related("batch", "batch__shipper")
        .filter(
            batch__assigned_at__date__gte=date_from,
            batch__assigned_at__date__lte=date_to,
            batch__assigned_at__isnull=False,
        )
        .order_by("batch__assigned_at", "id")
    )

    for row in cod_qs:
        batch = getattr(row, "batch", None)
        shipper = getattr(batch, "shipper", None) if batch else None
        shipper_name = getattr(shipper, "name", "") or "-"

        box = grouped[shipper_name]
        box["shipper_name"] = shipper_name
        box["cod_total"] += _to_decimal(getattr(row, "target_total_usd", 0))
        box["received"] += (
            _to_decimal(getattr(row, "cash_usd", 0))
            + _to_decimal(getattr(row, "aba_usd", 0))
        )

    done_qs = (
        PPDeliveryItem.objects
        .select_related("batch", "batch__shipper", "order")
        .filter(
            ticked=True,
            batch__assigned_at__date__gte=date_from,
            batch__assigned_at__date__lte=date_to,
            batch__assigned_at__isnull=False,
        )
    )

    for item in done_qs:
        if not _is_normal_item(item):
            continue

        batch = getattr(item, "batch", None)
        shipper = getattr(batch, "shipper", None) if batch else None
        shipper_name = getattr(shipper, "name", "") or "-"

        box = grouped[shipper_name]
        box["shipper_name"] = shipper_name
        box["done_orders"] += 1

    rows = []
    for _, box in grouped.items():
        balance = box["cod_total"] - box["received"]
        rows.append({
            "shipper_name": box["shipper_name"],
            "done_orders": box["done_orders"],
            "cod_total": float(box["cod_total"]),
            "received": float(box["received"]),
            "balance": float(balance),
        })

    rows.sort(key=lambda x: (-x["done_orders"], x["shipper_name"].lower()))
    return rows


def build_profit_dashboard(date_from: date, date_to: date):
    today_cards = _build_today_cards(date_to)
    trend_30_days = _build_trend_30_days(date_to)
    shipper_rows = _build_shipper_summary(date_from, date_to)

    return {
        "today_cards": today_cards,
        "trend_30_days": trend_30_days,
        "shipper_rows": shipper_rows,
    }