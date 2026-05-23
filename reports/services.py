from collections import OrderedDict
from datetime import datetime, time
from decimal import Decimal

from django.db.models import Q


# =========================================================
# REPORT STATUS RULES
# =========================================================
# DS Express Order.status examples:
# CREATED
# OUT_FOR_DELIVERY
# DELIVERED
# PROVINCE_ASSIGNED
# RETURN_ASSIGNED
# VOID
#
# Business rule for report:
# - DELIVERED / DONE / PROVINCE_ASSIGNED = DONE
# - RETURNED / RETURN_ASSIGNED = DONE RETURN
# - VOID = not pending
# - everything else = PENDING
# =========================================================

DONE_STATUSES = {
    "DELIVERED",
    "DONE",
    "PROVINCE_ASSIGNED",
}

RETURNED_STATUSES = {
    "RETURNED",
    "RETURN_ASSIGNED",
}

VOID_STATUSES = {
    "VOID",
}


def _start(d):
    """
    Convert date to start datetime.
    If already datetime, keep it.
    """
    if not d:
        return None

    if isinstance(d, datetime):
        return d

    return datetime.combine(d, time.min)


def _end(d):
    """
    Convert date to end datetime.
    If already datetime, keep it.
    """
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
    Internal row type used for row color + money rules:
    - done_return
    - done
    - pending

    Important:
    ProvinceOps completed orders use PROVINCE_ASSIGNED,
    so PROVINCE_ASSIGNED must count as done.
    """
    status = (getattr(order, "status", "") or "").upper().strip()

    if status in RETURNED_STATUSES:
        return "done_return"

    if status in DONE_STATUSES:
        return "done"

    return "pending"


def display_status(order):
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

    Business rule:
    - PENDING and RETURNED => COD/FEE show 0
    - DONE => show real COD/FEE
    """
    row_type = classify_row(order)

    if row_type in ("pending", "done_return"):
        return {
            "cod": Decimal("0.00"),
            "delivery_fee": Decimal("0.00"),
            "additional_fee": Decimal("0.00"),
            "total_fee": Decimal("0.00"),
        }

    delivery_fee = safe_decimal(getattr(order, "delivery_fee", 0))
    additional_fee = safe_decimal(getattr(order, "additional_fee", 0))
    total_fee = (delivery_fee + additional_fee).quantize(Decimal("0.00"))
    cod = safe_decimal(getattr(order, "cod", 0))

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
    """
    Done rows:
    - based on final status
    - includes PP done, province done, and return done
    - PP done usually has done_at
    - ProvinceOps done may not have done_at
    - ProvinceOps can fallback to delivery_date / created_at
    """
    qs = (
        Order.objects
        .select_related("seller", "delivery_shipper")
        .filter(
            is_deleted=False,
            status__in=(DONE_STATUSES | RETURNED_STATUSES),
        )
    )

    seller = cleaned.get("seller")
    if seller:
        qs = qs.filter(seller=seller)

    d_from = cleaned.get("delivery_date_from")
    d_to = cleaned.get("delivery_date_to")

    date_q = Q()

    if d_from and d_to:
        date_q = (
            Q(done_at__gte=d_from, done_at__lte=d_to)
            | Q(done_at__isnull=True, delivery_date__gte=d_from, delivery_date__lte=d_to)
            | Q(done_at__isnull=True, delivery_date__isnull=True, created_at__gte=d_from, created_at__lte=d_to)
        )

    elif d_from:
        date_q = (
            Q(done_at__gte=d_from)
            | Q(done_at__isnull=True, delivery_date__gte=d_from)
            | Q(done_at__isnull=True, delivery_date__isnull=True, created_at__gte=d_from)
        )

    elif d_to:
        date_q = (
            Q(done_at__lte=d_to)
            | Q(done_at__isnull=True, delivery_date__lte=d_to)
            | Q(done_at__isnull=True, delivery_date__isnull=True, created_at__lte=d_to)
        )

    if date_q:
        qs = qs.filter(date_q)

    return qs.distinct().order_by("seller_code", "seller_name", "id")


def get_pending_queryset(Order, cleaned):
    """
    Pending rows:
    - anything not done / not returned / not void
    - prevents duplicate because done/returned rows stay only in done queryset

    Important:
    PROVINCE_ASSIGNED is excluded from pending.
    RETURN_ASSIGNED is excluded from pending.
    """
    qs = (
        Order.objects
        .select_related("seller", "delivery_shipper")
        .filter(is_deleted=False)
        .exclude(status__in=(DONE_STATUSES | RETURNED_STATUSES | VOID_STATUSES))
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

    return qs.distinct().order_by("seller_code", "seller_name", "id")


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