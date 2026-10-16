from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta, datetime, time
from decimal import Decimal

from django.db.models import Sum, Count, F
from django.db.models.functions import TruncDate
from django.utils import timezone
from provincecod.models import ProvinceCODBatch, ProvinceCODItem

from deliverpp.models import ClearPPCOD, PPDeliveryBatch, PPDeliveryItem
from orders.models import Order
from provinceops.models import ProvinceBatch, ProvinceBatchItem


def _date_bounds(start_date, end_date):
    """Index-friendly local day range [start, next_day); no DATE(column) cast."""
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start_date, time.min), tz),
        timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz),
    )


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


def _get_obj_date(obj, field_names):
    """
    Get date from the first existing datetime/date field.
    Safe when some models do not have ticked_at/done_at.
    """
    if not obj:
        return None

    for field in field_names:
        value = getattr(obj, field, None)
        if not value:
            continue

        try:
            if hasattr(value, "date"):
                return value.date()
            return value
        except Exception:
            continue

    return None


def _is_return_order(order) -> bool:
    """
    Do not count return orders in customer pending report.
    """
    if not order:
        return False

    status = str(getattr(order, "status", "") or "").upper()
    if status in {
        "RETURN_ASSIGNED",
        "RETURNED",
        "DONE_RETURN",
        "RETURN",
        "RTS",
        "RET",
    }:
        return True

    tracking = str(
        getattr(order, "tracking_number", "")
        or getattr(order, "code", "")
        or getattr(order, "order_code", "")
        or ""
    ).upper()

    if tracking.startswith("RTS-") or tracking.startswith("RET-"):
        return True

    return False


def _is_normal_pp_item(item) -> bool:
    """
    Dashboard rule:
    - count only NORMAL PP item
    - do NOT count return item
    - do NOT count return batch
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
    if _is_return_order(order):
        return False

    batch = getattr(item, "batch", None)
    for attr_name in ["batch_code", "code", "name"]:
        value = str(getattr(batch, attr_name, "") or "").upper()
        if value.startswith("RTS-") or value.startswith("RET-"):
            return False

    return True


def _get_shop_name(order) -> str:
    seller = getattr(order, "seller", None)
    return (
        getattr(seller, "name", "")
        or getattr(order, "seller_name", "")
        or getattr(order, "shop_name", "")
        or "No Shop"
    )


def _is_order_done(order) -> bool:
    status = str(getattr(order, "status", "") or "").upper()
    return status in {
        "DELIVERED",
        "DONE",
        "COMPLETED",
        "COMPLETE",
        "PROVINCE_DONE",
        "DONE_PROVINCE",
    }


def _build_customer_send_report(date_from: date, date_to: date):
    """
    Customer Send Report:
    - Send Today = orders created in selected date range.
    - Done Today = parcels completed in selected date range, even if sent before.
    - Pending Today = selected range created orders still not done.
    - All Pending = all not-done orders, excluding return orders.
    - Show only shops that:
        1) send today / selected range, OR
        2) no send today but have done today.
      Hide shops that only have old pending.
    """

    grouped = defaultdict(lambda: {
        "shop_name": "-",
        "send_today": 0,
        "done_today": 0,
        "pending_today": 0,
        "all_pending": 0,
    })

    # =========================
    # 1) SEND TODAY / SELECTED RANGE
    # =========================
    created_orders = (
        Order.objects
        .select_related("seller")
        .filter(
            created_at__gte=_date_bounds(date_from, date_to)[0], created_at__lt=_date_bounds(date_from, date_to)[1],
        )
        .order_by("seller__name", "id")
    )

    for order in created_orders:
        if _is_return_order(order):
            continue

        shop_name = _get_shop_name(order)
        box = grouped[shop_name]
        box["shop_name"] = shop_name
        box["send_today"] += 1

    # =========================
    # 2) DONE TODAY / SELECTED RANGE - PP
    # =========================
    done_order_ids_all = set()

    pp_done_items = (
        PPDeliveryItem.objects
        .select_related("batch", "order", "order__seller")
        .filter(ticked=True)
        .order_by("id")
    )

    for item in pp_done_items:
        if not _is_normal_pp_item(item):
            continue

        order = getattr(item, "order", None)
        if not order or _is_return_order(order):
            continue

        done_order_ids_all.add(order.id)

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

        if date_from <= done_date <= date_to:
            shop_name = _get_shop_name(order)
            box = grouped[shop_name]
            box["shop_name"] = shop_name
            box["done_today"] += 1

    # =========================
    # 3) DONE TODAY / SELECTED RANGE - PROVINCE
    # =========================
    province_done_items = (
        ProvinceBatchItem.objects
        .select_related("batch", "order", "order__seller")
        .filter(batch__status=ProvinceBatch.STATUS_DONE)
        .order_by("id")
    )

    for item in province_done_items:
        order = getattr(item, "order", None)
        batch = getattr(item, "batch", None)

        if not order or not batch or _is_return_order(order):
            continue

        done_order_ids_all.add(order.id)

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

        if date_from <= done_date <= date_to:
            shop_name = _get_shop_name(order)
            box = grouped[shop_name]
            box["shop_name"] = shop_name
            box["done_today"] += 1

    # =========================
    # 4) PENDING TODAY
    # Created in selected range but not done now
    # =========================
    for order in created_orders:
        if _is_return_order(order):
            continue

        if _is_order_done(order):
            continue

        if order.id in done_order_ids_all:
            continue

        shop_name = _get_shop_name(order)
        box = grouped[shop_name]
        box["shop_name"] = shop_name
        box["pending_today"] += 1

    # =========================
    # 5) ALL PENDING
    # All not-done orders, excluding return
    # =========================
    all_orders = (
        Order.objects
        .select_related("seller")
        .all()
        .order_by("seller__name", "id")
    )

    for order in all_orders:
        if _is_return_order(order):
            continue

        if _is_order_done(order):
            continue

        if order.id in done_order_ids_all:
            continue

        shop_name = _get_shop_name(order)
        box = grouped[shop_name]
        box["shop_name"] = shop_name
        box["all_pending"] += 1

    rows = []
    total_send = 0
    total_done_today = 0
    total_pending_today = 0
    total_all_pending = 0

    for _, box in grouped.items():
        # IMPORTANT:
        # show only shops that send today OR have done today.
        # hide shops that only have old pending.
        if box["send_today"] == 0 and box["done_today"] == 0:
            continue

        total_send += box["send_today"]
        total_done_today += box["done_today"]
        total_pending_today += box["pending_today"]
        total_all_pending += box["all_pending"]

        rows.append({
            "shop_name": box["shop_name"],
            "send_today": box["send_today"],
            "done_today": box["done_today"],
            "pending_today": box["pending_today"],
            "all_pending": box["all_pending"],
        })

    rows.sort(key=lambda x: (
        -x["send_today"],
        -x["done_today"],
        -x["pending_today"],
        x["shop_name"].lower(),
    ))

    return {
        "total_send": total_send,
        "total_done_today": total_done_today,
        "total_pending_today": total_pending_today,
        "total_all_pending": total_all_pending,
        "rows": rows,
    }


def _build_shipper_done_today_report(date_from: date, date_to: date):
    """
    Shipper Done Today Report:
    - Done PP = PPDeliveryItem ticked in selected date range.
    - Done PV = ProvinceBatchItem under done ProvinceBatch in selected date range.
    """

    grouped = defaultdict(lambda: {
        "shipper_name": "-",
        "done_pp": 0,
        "done_pv": 0,
        "total_done": 0,
    })

    # =========================
    # PP DONE TODAY
    # =========================
    pp_done_items = (
        PPDeliveryItem.objects
        .select_related("batch", "batch__shipper", "order")
        .filter(ticked=True)
        .order_by("id")
    )

    for item in pp_done_items:
        if not _is_normal_pp_item(item):
            continue

        batch = getattr(item, "batch", None)
        shipper = getattr(batch, "shipper", None) if batch else None
        shipper_name = getattr(shipper, "name", "") or "No Shipper"

        done_date = _get_obj_date(item, [
            "ticked_at",
            "done_at",
            "delivered_at",
            "completed_at",
            "updated_at",
            "created_at",
        ])

        if not done_date:
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

        if date_from <= done_date <= date_to:
            box = grouped[shipper_name]
            box["shipper_name"] = shipper_name
            box["done_pp"] += 1

    # =========================
    # PROVINCE DONE TODAY
    # =========================
    pv_done_items = (
        ProvinceBatchItem.objects
        .select_related("batch", "batch__shipper", "order")
        .filter(batch__status=ProvinceBatch.STATUS_DONE)
        .order_by("id")
    )

    for item in pv_done_items:
        batch = getattr(item, "batch", None)
        if not batch:
            continue

        shipper = getattr(batch, "shipper", None)
        shipper_name = getattr(shipper, "name", "") or "No Shipper"

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

        if date_from <= done_date <= date_to:
            box = grouped[shipper_name]
            box["shipper_name"] = shipper_name
            box["done_pv"] += 1

    rows = []
    total_done_pp = 0
    total_done_pv = 0

    for _, box in grouped.items():
        box["total_done"] = box["done_pp"] + box["done_pv"]

        total_done_pp += box["done_pp"]
        total_done_pv += box["done_pv"]

        rows.append({
            "shipper_name": box["shipper_name"],
            "done_pp": box["done_pp"],
            "done_pv": box["done_pv"],
            "total_done": box["total_done"],
        })

    rows.sort(key=lambda x: (-x["total_done"], x["shipper_name"].lower()))

    return {
        "total_done_pp": total_done_pp,
        "total_done_pv": total_done_pv,
        "total_done": total_done_pp + total_done_pv,
        "rows": rows,
    }


def _build_today_cards(target_date: date):
    created_count = Order.objects.filter(created_at__gte=_date_bounds(target_date, target_date)[0], created_at__lt=_date_bounds(target_date, target_date)[1]).count()

    sent_pp = 0
    pp_batch_qs = (
        PPDeliveryBatch.objects
        .select_related("shipper")
        .prefetch_related("items", "items__order")
        .filter(assigned_at__gte=_date_bounds(target_date, target_date)[0], assigned_at__lt=_date_bounds(target_date, target_date)[1])
        .order_by("assigned_at", "id")
    )

    for batch in pp_batch_qs:
        prefetched_items = getattr(batch, "_prefetched_objects_cache", {}).get("items")
        items = list(prefetched_items) if prefetched_items is not None else list(
            batch.items.select_related("order").all()
        )
        sent_pp += sum(1 for item in items if _is_normal_pp_item(item))

    sent_province = ProvinceBatchItem.objects.filter(
        batch__assigned_at__gte=_date_bounds(target_date, target_date)[0], batch__assigned_at__lt=_date_bounds(target_date, target_date)[1],
        batch__assigned_at__isnull=False,
        batch__status__in=[ProvinceBatch.STATUS_PENDING, ProvinceBatch.STATUS_DONE],
    ).count()

    done_pp_qs = PPDeliveryItem.objects.select_related("batch", "order").filter(
        ticked=True,
        batch__assigned_at__gte=_date_bounds(target_date, target_date)[0], batch__assigned_at__lt=_date_bounds(target_date, target_date)[1],
        batch__assigned_at__isnull=False,
    )
    done_pp = sum(1 for item in done_pp_qs if _is_normal_pp_item(item))

    done_province = ProvinceBatchItem.objects.filter(
        batch__assigned_at__gte=_date_bounds(target_date, target_date)[0], batch__assigned_at__lt=_date_bounds(target_date, target_date)[1],
        batch__assigned_at__isnull=False,
        batch__status=ProvinceBatch.STATUS_DONE,
    ).count()

    order_fee_agg = Order.objects.filter(created_at__gte=_date_bounds(target_date, target_date)[0], created_at__lt=_date_bounds(target_date, target_date)[1]).aggregate(
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
        .filter(batch__assigned_at__gte=_date_bounds(target_date, target_date)[0], batch__assigned_at__lt=_date_bounds(target_date, target_date)[1])
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

    # Single grouped SQL query instead of 30 separate aggregates and 30 counts.
    for row in (Order.objects.filter(
        created_at__gte=_date_bounds(start_date, end_date)[0], created_at__lt=_date_bounds(start_date, end_date)[1]
    ).annotate(day=TruncDate("created_at")).values("day").annotate(
        created_count=Count("id"),
        delivery_fee_total=Sum("delivery_fee"),
        additional_fee_total=Sum("additional_fee"),
        province_fee_total=Sum("province_fee"),
    )):
        d = row["day"]
        if d not in trend_map:
            continue
        delivery = _to_decimal(row["delivery_fee_total"])
        expense = _to_decimal(row["additional_fee_total"]) + _to_decimal(row["province_fee_total"])
        trend_map[d]["created"] = row["created_count"]
        trend_map[d]["shipment_fee"] = float(delivery)
        trend_map[d]["expense"] = float(expense)
        trend_map[d]["revenue"] = float(delivery - expense)

    done_pp_qs = (
        PPDeliveryItem.objects
        .select_related("batch", "order")
        .filter(
            ticked=True,
            batch__assigned_at__gte=_date_bounds(start_date, end_date)[0], batch__assigned_at__lt=_date_bounds(start_date, end_date)[1],
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
            batch__assigned_at__gte=_date_bounds(start_date, end_date)[0], batch__assigned_at__lt=_date_bounds(start_date, end_date)[1],
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
            batch__assigned_at__gte=_date_bounds(date_from, date_to)[0], batch__assigned_at__lt=_date_bounds(date_from, date_to)[1],
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
            batch__assigned_at__gte=_date_bounds(date_from, date_to)[0], batch__assigned_at__lt=_date_bounds(date_from, date_to)[1],
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
            batch__assigned_at__gte=_date_bounds(date_from, date_to)[0], batch__assigned_at__lt=_date_bounds(date_from, date_to)[1],
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

        if _is_return_order(order):
            continue

        shipper = getattr(batch, "shipper", None) if batch else None
        shipper_name = getattr(shipper, "name", "") or "No Shipper"

        shop_name = _get_shop_name(order) if order else "No Shop"

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



def _carrier_type(name):
    """COD VET and VET are one company; same for J&T."""
    name = " ".join((name or "").upper().replace("&", " AND ").split())
    if "VET" in name:
        return "VET"
    if "J&T" in name or "J AND T" in name or "JNT" in name or "J T" == name:
        return "J&T"
    return None


def _estimated_carrier_fee_khr(carrier, *, via_cod_province=False, shipper_label=""):
    """Estimated cost per dispatch, in KHR (not the seller's delivery fee).

    Province Assign: VET 5,000; COD VET 8,000.
    COD Province: VET or COD VET 8,000.
    J&T (including COD J&T): 6,000 in either workflow.
    """
    if carrier == "J&T":
        return 6000
    if carrier == "VET":
        return 8000 if via_cod_province or "COD" in (shipper_label or "").upper() else 5000
    return 0


def _build_carrier_report(start_date, end_date):
    """Unique shipments; COD Province takes precedence over normal Province.

    Never use the order's current shipper as proof of its historic dispatch.
    The batch's assigned shipper and timestamp describe the shipment.
    """
    shipments = {}

    normal = ProvinceBatchItem.objects.filter(
        batch__status__in=[ProvinceBatch.STATUS_PENDING, ProvinceBatch.STATUS_DONE],
        batch__assigned_at__gte=_date_bounds(start_date, end_date)[0], batch__assigned_at__lt=_date_bounds(start_date, end_date)[1],
        batch__shipper__isnull=False,
    ).select_related("batch__shipper", "order").order_by("id")
    for item in normal.iterator(chunk_size=1000):
        if _is_return_order(item.order):
            continue
        name = _carrier_type(item.batch.shipper.name)
        if not name:
            continue
        day = timezone.localtime(item.batch.assigned_at).date()
        cod_label = "COD" in (item.batch.shipper.name or "").upper()
        fee = _estimated_carrier_fee_khr(name, shipper_label=item.batch.shipper.name)
        old = shipments.get(item.order_id)
        if old is None or (old[3] == 0 and cod_label):
            shipments[item.order_id] = (day, name, fee, int(cod_label))

    cod_shipments = ProvinceCODItem.objects.filter(
        batch__status=ProvinceCODBatch.STATUS_SENT,
        batch__shipper__isnull=False,
    ).filter(
        sent_at__gte=_date_bounds(start_date, end_date)[0], sent_at__lt=_date_bounds(start_date, end_date)[1]
    ).select_related("batch__shipper", "order").order_by("id")
    for item in cod_shipments.iterator(chunk_size=1000):
        if _is_return_order(item.order):
            continue
        name = _carrier_type(item.batch.shipper.name)
        if not name:
            continue
        dt = item.sent_at
        if not dt:
            continue
        day = timezone.localtime(dt).date()
        # Any COD Province shipment through VET always costs 8,000 riel,
        # even when the shipper field is simply 'VET'.
        shipments[item.order_id] = (
            day, name, _estimated_carrier_fee_khr(name, via_cod_province=True), 2
        )

    days = _daterange(start_date, end_date)
    summary = {carrier: {"name": carrier, "orders": 0, "fee_khr": 0} for carrier in ("VET", "J&T")}
    daily = {d: {"date": d, "VET": 0, "J&T": 0, "vet_fee": 0, "jt_fee": 0, "vet_orders": 0, "jt_orders": 0} for d in days}
    for day, name, fee, _source in shipments.values():
        summary[name]["orders"] += 1
        summary[name]["fee_khr"] += fee
        daily[day][name] += 1
        daily[day]["vet_orders" if name == "VET" else "jt_orders"] += 1
        daily[day]["vet_fee" if name == "VET" else "jt_fee"] += fee
    return {"summary": list(summary.values()), "daily": list(reversed(list(daily.values()))),
            "total_orders": sum(r["orders"] for r in summary.values()),
            "total_fee_khr": sum(r["fee_khr"] for r in summary.values())}


def _build_carrier_30_days(end_date):
    return _build_carrier_report(end_date - timedelta(days=29), end_date)


def build_profit_dashboard(date_from: date, date_to: date):
    today_cards = _build_today_cards(date_to)
    trend_30_days = _build_trend_30_days(date_to)
    shipper_rows = _build_shipper_summary(date_from, date_to)
    province_send_report = _build_province_send_report(date_from, date_to)
    # Cost estimate follows the selected report dates. Unlike the simple province
    # counts, it also includes COD Province and deduplicates orders across both.
    province_send_report["carrier_costs"] = _build_carrier_report(date_from, date_to)
    customer_send_report = _build_customer_send_report(date_from, date_to)
    shipper_done_today_report = _build_shipper_done_today_report(date_from, date_to)

    return {
        "carrier_fees": _build_carrier_30_days(date_to),
        "today_cards": today_cards,
        "trend_30_days": trend_30_days,
        "shipper_rows": shipper_rows,
        "province_send_report": province_send_report,
        "customer_send_report": customer_send_report,
        "shipper_done_today_report": shipper_done_today_report,
    }