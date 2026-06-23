from collections import OrderedDict
from datetime import datetime, time
from decimal import Decimal

from django.db.models import Q

from provincecod.models import ProvinceCODBatch, ProvinceCODItem
from provinceops.models import ProvinceBatch, ProvinceBatchItem


# =========================================================
# REPORT STATUS RULES
# =========================================================
DONE_STATUSES = {
    "DELIVERED",
    "DONE",
    "PROVINCE_ASSIGNED",
}

RETURNED_STATUSES = {
    "RETURNED",
    "RETURN_ASSIGNED",
    "DONE_RETURN",
}

VOID_STATUSES = {
    "VOID",
}


def _start(d):
    if not d:
        return None

    if isinstance(d, datetime):
        return d

    return datetime.combine(d, time.min)


def _end(d):
    if not d:
        return None

    if isinstance(d, datetime):
        return d

    return datetime.combine(d, time.max)


def safe_text(v):
    return "" if v is None else str(v)


def safe_decimal(v):
    try:
        return Decimal(str(v or 0)).quantize(Decimal("0.00"))
    except Exception:
        return Decimal("0.00")


def get_shipper_name(order):
    """
    Report shipper display priority:
    1. delivery_shipper FK
    2. province / assigned shipper fallback
    3. text fallback
    """
    delivery_shipper = getattr(order, "delivery_shipper", None)
    if delivery_shipper:
        name = getattr(delivery_shipper, "name", "") or ""
        if name.strip():
            return name.strip()

    for attr in [
        "shipper",
        "assigned_shipper",
        "province_shipper",
        "province_assigned_shipper",
        "return_shipper",
    ]:
        obj = getattr(order, attr, None)
        if obj:
            name = getattr(obj, "name", "") or ""
            if name.strip():
                return name.strip()

    for attr in [
        "shipper_name",
        "delivery_shipper_name",
        "province_shipper_name",
        "assigned_shipper_name",
    ]:
        value = getattr(order, attr, "") or ""
        if str(value).strip():
            return str(value).strip()

    return "-"


def classify_row(order):
    """
    Internal row type:
    - done_return
    - done
    - pending

    Province COD orders stay internally classified as done so they remain
    inside the completed section and completed totals.
    """
    status = (getattr(order, "status", "") or "").upper().strip()

    if status in RETURNED_STATUSES:
        return "done_return"

    if status in DONE_STATUSES:
        return "done"

    return "pending"


def _latest_active_province_cod_item(order):
    """
    Return the latest Province COD item that is not inside a cancelled batch.

    Uses prefetched data when available. Otherwise it safely queries the
    Province COD table.
    """
    prefetched_cache = getattr(order, "_prefetched_objects_cache", {}) or {}
    prefetched_items = prefetched_cache.get("province_cod_items")

    if prefetched_items is not None:
        active_items = [
            item
            for item in prefetched_items
            if (
                getattr(getattr(item, "batch", None), "status", "")
                != ProvinceCODBatch.STATUS_CANCELLED
            )
        ]

        if not active_items:
            return None

        return max(
            active_items,
            key=lambda item: getattr(item, "id", 0) or 0,
        )

    return (
        ProvinceCODItem.objects
        .select_related("batch")
        .filter(order_id=getattr(order, "id", None))
        .exclude(batch__status=ProvinceCODBatch.STATUS_CANCELLED)
        .order_by("-id")
        .first()
    )


def display_status(order):
    """
    Display status used by Delivery Report.

    Province COD workflow:
    - SENT / RECEIVED / PAID => SENT COD
    - RETURNED => RETURNED

    The Order itself can remain DONE internally.
    """
    province_cod_item = _latest_active_province_cod_item(order)

    if province_cod_item:
        cod_status = (
            getattr(province_cod_item, "cod_status", "")
            or ""
        ).upper().strip()

        batch_status = (
            getattr(
                getattr(province_cod_item, "batch", None),
                "status",
                "",
            )
            or ""
        ).upper().strip()

        if cod_status == ProvinceCODItem.STATUS_RETURNED:
            return "RETURNED"

        if (
            cod_status
            in {
                ProvinceCODItem.STATUS_SENT,
                ProvinceCODItem.STATUS_RECEIVED,
                ProvinceCODItem.STATUS_PAID,
            }
            or batch_status == ProvinceCODBatch.STATUS_SENT
        ):
            return "SENT COD"

    row_type = classify_row(order)

    if row_type == "done_return":
        return "RETURNED"

    if row_type == "done":
        return "DONE"

    return "PENDING"


def report_money(order):
    """
    Display-only money values for report.
    Do NOT change DB.

    Pending and returned rows show 0.
    Done rows, including SENT COD, use the current order money values.
    """
    row_type = classify_row(order)

    if row_type in ("pending", "done_return"):
        return {
            "cod": Decimal("0.00"),
            "delivery_fee": Decimal("0.00"),
            "additional_fee": Decimal("0.00"),
            "province_fee": Decimal("0.00"),
            "total_fee": Decimal("0.00"),
        }

    delivery_fee = safe_decimal(getattr(order, "delivery_fee", 0))
    additional_fee = safe_decimal(getattr(order, "additional_fee", 0))
    province_fee = safe_decimal(getattr(order, "province_fee", 0))

    total_fee = (
        delivery_fee
        + additional_fee
        + province_fee
    ).quantize(Decimal("0.00"))

    cod = safe_decimal(getattr(order, "cod", 0))

    return {
        "cod": cod,
        "delivery_fee": delivery_fee,
        "additional_fee": additional_fee,
        "province_fee": province_fee,
        "total_fee": total_fee,
    }


def get_status_sort_key(order):
    row_type = classify_row(order)

    if row_type == "done":
        return 1

    if row_type == "done_return":
        return 2

    return 3


def sort_report_rows(rows):
    def _key(o):
        done_at = getattr(o, "done_at", None)
        created_at = getattr(o, "created_at", None)
        tracking_no = getattr(o, "tracking_no", "") or ""
        oid = getattr(o, "id", 0) or 0

        return (
            get_status_sort_key(o),
            str(done_at or ""),
            str(created_at or ""),
            str(tracking_no),
            oid,
        )

    return sorted(rows, key=_key)


def _province_done_order_ids(d_from=None, d_to=None, seller=None):
    """
    Province DONE fix:
    Delivery Report must include orders from province batches where:
    - ProvinceBatch.status = DONE
    - ProvinceBatch.assigned_at is inside the report delivery date range

    This avoids using Order.delivery_date because the Order model does not
    have that field.
    """
    qs = (
        ProvinceBatchItem.objects
        .select_related("batch", "order", "order__seller")
        .filter(
            batch__status=ProvinceBatch.STATUS_DONE,
            batch__assigned_at__isnull=False,
            order__is_deleted=False,
        )
    )

    if d_from:
        qs = qs.filter(batch__assigned_at__gte=_start(d_from))

    if d_to:
        qs = qs.filter(batch__assigned_at__lte=_end(d_to))

    if seller:
        qs = qs.filter(order__seller=seller)

    return qs.values_list("order_id", flat=True)


def get_done_queryset(Order, cleaned):
    """
    Done rows:
    - Normal / PP done: from Order.done_at
    - Province done: from ProvinceBatchItem.batch.assigned_at
    - Province COD orders remain in done results but display SENT COD
    - No delivery_date field is used
    """
    seller = cleaned.get("seller")
    d_from = cleaned.get("delivery_date_from")
    d_to = cleaned.get("delivery_date_to")

    base_qs = (
        Order.objects
        .select_related("seller", "delivery_shipper")
        .prefetch_related("province_cod_items__batch")
        .filter(
            is_deleted=False,
            status__in=(DONE_STATUSES | RETURNED_STATUSES),
        )
    )

    if seller:
        base_qs = base_qs.filter(seller=seller)

    date_q = Q()

    if d_from and d_to:
        date_q = (
            Q(
                done_at__gte=_start(d_from),
                done_at__lte=_end(d_to),
            )
            | Q(
                done_at__isnull=True,
                created_at__gte=_start(d_from),
                created_at__lte=_end(d_to),
            )
        )

    elif d_from:
        date_q = (
            Q(done_at__gte=_start(d_from))
            | Q(
                done_at__isnull=True,
                created_at__gte=_start(d_from),
            )
        )

    elif d_to:
        date_q = (
            Q(done_at__lte=_end(d_to))
            | Q(
                done_at__isnull=True,
                created_at__lte=_end(d_to),
            )
        )

    normal_done_qs = base_qs

    if date_q:
        normal_done_qs = normal_done_qs.filter(date_q)

    province_ids = _province_done_order_ids(
        d_from=d_from,
        d_to=d_to,
        seller=seller,
    )

    province_done_qs = (
        Order.objects
        .select_related("seller", "delivery_shipper")
        .prefetch_related("province_cod_items__batch")
        .filter(
            is_deleted=False,
            id__in=province_ids,
        )
    )

    return (
        normal_done_qs
        | province_done_qs
    ).distinct().order_by(
        "seller_code",
        "seller_name",
        "id",
    )


def get_pending_queryset(Order, cleaned):
    """
    Pending rows:
    anything not done / not returned / not void.
    """
    qs = (
        Order.objects
        .select_related("seller", "delivery_shipper")
        .prefetch_related("province_cod_items__batch")
        .filter(is_deleted=False)
        .exclude(
            status__in=(
                DONE_STATUSES
                | RETURNED_STATUSES
                | VOID_STATUSES
            )
        )
    )

    seller = cleaned.get("seller")

    if seller:
        qs = qs.filter(seller=seller)

    p_from = cleaned.get("pending_date_from")
    p_to = cleaned.get("pending_date_to")

    if p_from:
        qs = qs.filter(created_at__gte=_start(p_from))

    if p_to:
        qs = qs.filter(created_at__lte=_end(p_to))

    return qs.distinct().order_by(
        "seller_code",
        "seller_name",
        "id",
    )


def group_by_seller(rows):
    grouped = OrderedDict()

    for o in rows:
        if getattr(o, "seller", None):
            shop_code = getattr(o.seller, "code", "") or ""
            shop_name = getattr(o.seller, "name", "") or ""

            if shop_code and shop_name:
                key = f"{shop_code} - {shop_name}"
            elif shop_name:
                key = shop_name
            elif shop_code:
                key = shop_code
            else:
                key = "No Shop"

        else:
            seller_code = getattr(o, "seller_code", "") or ""
            seller_name = getattr(o, "seller_name", "") or ""

            if seller_code and seller_name:
                key = f"{seller_code} - {seller_name}"
            elif seller_name:
                key = seller_name
            elif seller_code:
                key = seller_code
            else:
                key = "No Shop"

        grouped.setdefault(key, []).append(o)

    sorted_grouped = OrderedDict()

    for key, seller_rows in grouped.items():
        sorted_grouped[key] = sort_report_rows(seller_rows)

    return sorted_grouped


def calc_totals(rows):
    total_cod = Decimal("0.00")
    total_fee = Decimal("0.00")

    for o in rows:
        money = report_money(o)
        total_cod += Decimal(str(money["cod"] or 0))
        total_fee += Decimal(str(money["total_fee"] or 0))

    total_cod = total_cod.quantize(Decimal("0.00"))
    total_fee = total_fee.quantize(Decimal("0.00"))
    pay = (total_cod - total_fee).quantize(Decimal("0.00"))

    return total_cod, total_fee, pay
