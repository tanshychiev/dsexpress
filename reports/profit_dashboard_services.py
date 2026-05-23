from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

from deliverpp.models import ClearPPCOD, PPDeliveryBatch, PPDeliveryItem
from orders.models import Order
from provinceops.models import ProvinceBatch, ProvinceBatchItem


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


def _calc_done_percent(done_count, sent_count):
    if not sent_count:
        return 0
    return round((done_count / sent_count) * 100, 2)


def _is_normal_pp_item(item) -> bool:
    """
    Dashboard rule:
    - count only NORMAL PP done pc
    - do NOT count return done pc
    - do NOT count return done batch
    """
    source_type = str(getattr(item, "source_type", "") or "").upper()

    source_normal = str(getattr(PPDeliveryItem, "SOURCE_NORMAL", "NORMAL")).upper()
    source_return = str(getattr(PPDeliveryItem, "SOURCE_RETURN", "RETURN")).upper()

    if source_type:
        if source_type == source_return:
            return False
        if source_type == source_normal:
            return True

    order = getattr(item, "order", None)
    order_status = str(getattr(order, "status", "") or "").upper()
    if order_status in {"RETURN_ASSIGNED", "RETURNED", "DONE_RETURN"}:
        return False

    batch = getattr(item, "batch", None)
    for attr_name in ["batch_code", "code", "name"]:
        value = str(getattr(batch, attr_name, "") or "").upper()
        if value.startswith("RTS-") or value.startswith("RET-"):
            return False

    return True


def _build_today_cards(target_date: date):
    created_count = Order.objects.filter(created_at__date=target_date).count()

    sent_pp = 0
    pp_batch_qs = (
        PPDeliveryBatch.objects
        .select_related("shipper")
        .prefetch_related("items", "items__order")
        .filter(assigned_at__date=target_date)
        .order_by("assigned_at", "id")
    )

    for batch in pp_batch_qs:
        prefetched_items = getattr(batch, "_prefetched_objects_cache", {}).get("items")
        items = list(prefetched_items) if prefetched_items is not None else list(
            batch.items.select_related("order").all()
        )
        sent_pp += sum(1 for item in items if _is_normal_pp_item(item))

    sent_province = ProvinceBatchItem.objects.filter(
        batch__assigned_at__date=target_date,
        batch__assigned_at__isnull=False,
        batch__status__in=[ProvinceBatch.STATUS_PENDING, ProvinceBatch.STATUS_DONE],
    ).count()

    done_pp_qs = PPDeliveryItem.objects.select_related("batch", "order").filter(
        ticked=True,
        batch__assigned_at__date=target_date,
        batch__assigned_at__isnull=False,
    )
    done_pp = sum(1 for item in done_pp_qs if _is_normal_pp_item(item))

    done_province = ProvinceBatchItem.objects.filter(
        batch__assigned_at__date=target_date,
        batch__assigned_at__isnull=False,
        batch__status=ProvinceBatch.STATUS_DONE,
    ).count()

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

    done_pp_qs = (
        PPDeliveryItem.objects
        .select_related("batch", "order")
        .filter(
            ticked=True,
            batch__assigned_at__date__gte=start_date,
            batch__assigned_at__date__lte=end_date,
            batch__assigned_at__isnull=False,
        )
        .order_by("batch__assigned_at", "id")
    )

    for item in done_pp_qs:
        if not _is_normal_pp_item(item):
            continue

        batch = getattr(item, "batch", None)
        assigned_at = getattr(batch, "assigned_at", None) if batch else None
        if not assigned_at:
            continue

        d = assigned_at.date()
        if d in trend_map:
            trend_map[d]["done_pp"] += 1

    done_province_qs = (
        ProvinceBatchItem.objects
        .select_related("batch")
        .filter(
            batch__assigned_at__date__gte=start_date,
            batch__assigned_at__date__lte=end_date,
            batch__assigned_at__isnull=False,
            batch__status=ProvinceBatch.STATUS_DONE,
        )
        .order_by("batch__assigned_at", "id")
    )

    for item in done_province_qs:
        batch = getattr(item, "batch", None)
        assigned_at = getattr(batch, "assigned_at", None) if batch else None
        if not assigned_at:
            continue

        d = assigned_at.date()
        if d in trend_map:
            trend_map[d]["done_province"] += 1

    for d in days:
        trend_map[d]["total_done"] = trend_map[d]["done_pp"] + trend_map[d]["done_province"]

    return [trend_map[d] for d in days]


def _build_shipper_summary(date_from: date, date_to: date):
    grouped = defaultdict(lambda: {
        "shipper_name": "-",
        "done_orders": 0,
        "cod_total": ZERO,
        "received": ZERO,
        "balance": ZERO,
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

    done_pp_qs = (
        PPDeliveryItem.objects
        .select_related("batch", "batch__shipper", "order")
        .filter(
            ticked=True,
            batch__assigned_at__date__gte=date_from,
            batch__assigned_at__date__lte=date_to,
            batch__assigned_at__isnull=False,
        )
    )

    for item in done_pp_qs:
        if not _is_normal_pp_item(item):
            continue

        batch = getattr(item, "batch", None)
        shipper = getattr(batch, "shipper", None) if batch else None
        shipper_name = getattr(shipper, "name", "") or "-"

        box = grouped[shipper_name]
        box["shipper_name"] = shipper_name
        box["done_orders"] += 1

    rows = []
    for _, box in grouped.items():
        box["balance"] = box["cod_total"] - box["received"]
        rows.append({
            "shipper_name": box["shipper_name"],
            "done_orders": box["done_orders"],
            "cod_total": float(box["cod_total"]),
            "received": float(box["received"]),
            "balance": float(box["balance"]),
        })

    rows.sort(key=lambda x: (-x["done_orders"], x["shipper_name"].lower()))
    return rows


def _build_province_send_report(date_from: date, date_to: date):
    total_send = 0

    shipper_map = defaultdict(int)
    shop_map = defaultdict(lambda: {
        "shop_name": "-",
        "total": 0,
        "shipper_counts": defaultdict(int),
    })

    qs = (
        ProvinceBatchItem.objects
        .select_related("batch", "batch__shipper", "order", "order__seller")
        .filter(
            batch__assigned_at__date__gte=date_from,
            batch__assigned_at__date__lte=date_to,
            batch__assigned_at__isnull=False,
            batch__status__in=[
                ProvinceBatch.STATUS_PENDING,
                ProvinceBatch.STATUS_DONE,
            ],
        )
        .order_by("batch__assigned_at", "id")
    )

    for item in qs:
        batch = getattr(item, "batch", None)
        order = getattr(item, "order", None)

        shipper = getattr(batch, "shipper", None) if batch else None
        shipper_name = getattr(shipper, "name", "") or "No Shipper"

        seller = getattr(order, "seller", None) if order else None
        shop_name = (
            getattr(seller, "name", "")
            or getattr(order, "seller_name", "")
            or "No Shop"
        )

        total_send += 1
        shipper_map[shipper_name] += 1

        shop_box = shop_map[shop_name]
        shop_box["shop_name"] = shop_name
        shop_box["total"] += 1
        shop_box["shipper_counts"][shipper_name] += 1

    shipper_rows = [
        {
            "shipper_name": name,
            "total": count,
        }
        for name, count in shipper_map.items()
    ]
    shipper_rows.sort(key=lambda x: (-x["total"], x["shipper_name"].lower()))

    shipper_columns = [r["shipper_name"] for r in shipper_rows]

    shop_rows = []
    for _, box in shop_map.items():
        shop_rows.append({
            "shop_name": box["shop_name"],
            "total": box["total"],
            "shipper_counts": [
                box["shipper_counts"].get(shipper_name, 0)
                for shipper_name in shipper_columns
            ],
        })

    shop_rows.sort(key=lambda x: (-x["total"], x["shop_name"].lower()))

    shop_shipper_chart = []
    for row in shop_rows:
        for idx, shipper_name in enumerate(shipper_columns):
            count = row["shipper_counts"][idx]
            if count:
                shop_shipper_chart.append({
                    "label": f"{row['shop_name']} - {shipper_name}",
                    "total": count,
                })

    return {
        "total_send": total_send,

        "shipper_rows": shipper_rows,
        "shipper_columns": shipper_columns,
        "shop_rows": shop_rows,

        "shipper_chart_labels": [r["shipper_name"] for r in shipper_rows],
        "shipper_chart_data": [r["total"] for r in shipper_rows],

        "shop_shipper_chart_labels": [r["label"] for r in shop_shipper_chart],
        "shop_shipper_chart_data": [r["total"] for r in shop_shipper_chart],
    }


def build_profit_dashboard(date_from: date, date_to: date):
    today_cards = _build_today_cards(date_to)
    trend_30_days = _build_trend_30_days(date_to)
    shipper_rows = _build_shipper_summary(date_from, date_to)
    province_send_report = _build_province_send_report(date_from, date_to)

    return {
        "today_cards": today_cards,
        "trend_30_days": trend_30_days,
        "shipper_rows": shipper_rows,
        "province_send_report": province_send_report,
    }