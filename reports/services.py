from collections import OrderedDict
from datetime import datetime, time
from decimal import Decimal


DONE_STATUSES = {"DELIVERED", "DONE"}
RETURNED_STATUSES = {"RETURNED"}
VOID_STATUSES = {"VOID"}


def _start(d):
    return datetime.combine(d, time.min)


def _end(d):
    return datetime.combine(d, time.max)


def safe_text(v):
    return "" if v is None else str(v)


def get_shipper_name(order):
    """
    Report shipper display priority:
    1. delivery_shipper FK
    2. other possible FK fallback
    3. text fallback
    """
    delivery_shipper = getattr(order, "delivery_shipper", None)
    if delivery_shipper:
        name = getattr(delivery_shipper, "name", "") or ""
        if name.strip():
            return name.strip()

    for attr in ["shipper", "assigned_shipper", "province_shipper"]:
        obj = getattr(order, attr, None)
        if obj:
            name = getattr(obj, "name", "") or ""
            if name.strip():
                return name.strip()

    for attr in ["shipper_name", "delivery_shipper_name"]:
        value = getattr(order, attr, "") or ""
        if str(value).strip():
            return str(value).strip()

    return "-"


def classify_row(order):
    """
    Internal types used for row color + money rules:
    - done_return (green)
    - done (white)
    - pending (yellow)

    Business rule:
    - report follows current order status
    """
    status = (getattr(order, "status", "") or "").upper()

    if status in RETURNED_STATUSES:
        return "done_return"

    if status in DONE_STATUSES:
        return "done"

    return "pending"


def display_status(order):
    t = classify_row(order)

    if t == "done_return":
        return "RETURNED"
    if t == "done":
        return "DONE"
    return "PENDING"


def report_money(order):
    """
    Display-only money values for report (do NOT change DB):
    - PENDING and RETURNED => COD/FEE must be 0
    - DONE => show real COD/FEE
    """
    t = classify_row(order)

    if t in ("pending", "done_return"):
        return {
            "cod": Decimal("0.00"),
            "delivery_fee": Decimal("0.00"),
            "additional_fee": Decimal("0.00"),
            "total_fee": Decimal("0.00"),
        }

    delivery_fee = Decimal(str(getattr(order, "delivery_fee", 0) or 0)).quantize(Decimal("0.00"))
    additional_fee = Decimal(str(getattr(order, "additional_fee", 0) or 0)).quantize(Decimal("0.00"))
    total_fee = (delivery_fee + additional_fee).quantize(Decimal("0.00"))
    cod = Decimal(str(getattr(order, "cod", 0) or 0)).quantize(Decimal("0.00"))

    return {
        "cod": cod,
        "delivery_fee": delivery_fee,
        "additional_fee": additional_fee,
        "total_fee": total_fee,
    }


def get_status_sort_key(order):
    """
    Sort order:
    1. DONE
    2. RETURNED
    3. PENDING
    """
    row_type = classify_row(order)

    if row_type == "done":
        return 1
    if row_type == "done_return":
        return 2
    return 3


def sort_report_rows(rows):
    def _key(o):
        delivery_date = getattr(o, "delivery_date", None)
        done_at = getattr(o, "done_at", None)
        created_at = getattr(o, "created_at", None)
        tracking_no = getattr(o, "tracking_no", "") or ""
        oid = getattr(o, "id", 0) or 0

        return (
            get_status_sort_key(o),
            str(delivery_date or ""),
            str(done_at or ""),
            str(created_at or ""),
            str(tracking_no),
            oid,
        )

    return sorted(rows, key=_key)


def get_done_queryset(Order, cleaned):
    qs = (
        Order.objects
        .select_related("seller", "delivery_shipper")
        .filter(is_deleted=False)
    )

    seller = cleaned.get("seller")
    if seller:
        qs = qs.filter(seller=seller)

    d_from = cleaned.get("delivery_date_from")
    d_to = cleaned.get("delivery_date_to")

    qs = qs.filter(status__in=(DONE_STATUSES | RETURNED_STATUSES))

    if d_from:
        qs = qs.filter(done_at__gte=d_from)
    if d_to:
        qs = qs.filter(done_at__lte=d_to)

    return qs.order_by("seller_code", "seller_name", "id")


def get_pending_queryset(Order, cleaned):
    qs = (
        Order.objects
        .select_related("seller", "delivery_shipper")
        .filter(is_deleted=False)
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

    qs = qs.exclude(status__in=(DONE_STATUSES | RETURNED_STATUSES)).exclude(status__in=VOID_STATUSES)

    return qs.order_by("seller_code", "seller_name", "id")


def group_by_seller(rows):
    grouped = OrderedDict()

    for o in rows:
        if getattr(o, "seller", None):
            shop_code = getattr(o.seller, "code", "") or ""
            shop_name = getattr(o.seller, "name", "") or ""
            key = f"{shop_code} - {shop_name}" if shop_code else shop_name
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
        m = report_money(o)
        total_cod += Decimal(str(m["cod"] or 0))
        total_fee += Decimal(str(m["total_fee"] or 0))

    pay = (total_cod - total_fee).quantize(Decimal("0.00"))
    return total_cod, total_fee, pay