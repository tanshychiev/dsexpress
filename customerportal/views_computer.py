from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from orders.models import Order
from inventory.models import InventorySellerSetting
from inventory.services import (
    get_seller_current_stock,
    get_seller_inventory_setting,
)

from .views import get_user_seller


ZERO = Decimal("0.00")


# =========================================================
# COMMON HELPERS
# =========================================================

def _parse_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except (TypeError, ValueError):
        return None


def _safe_rate(part, total):
    if not total:
        return 0

    try:
        value = round((part * 100.0) / total, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0

    return min(max(value, 0), 100)


def _get_report_range(request):
    today = timezone.localdate()
    period = (request.GET.get("period") or "last_30_days").strip()

    if period == "today":
        start_date = today
        end_date = today

    elif period == "yesterday":
        start_date = today - timedelta(days=1)
        end_date = start_date

    elif period == "last_7_days":
        start_date = today - timedelta(days=6)
        end_date = today

    elif period == "last_30_days":
        start_date = today - timedelta(days=29)
        end_date = today

    elif period == "this_month":
        start_date = today.replace(day=1)
        end_date = today

    elif period == "custom":
        start_date = _parse_date(request.GET.get("from"))
        end_date = _parse_date(request.GET.get("to"))

        if not start_date or not end_date:
            period = "last_30_days"
            start_date = today - timedelta(days=29)
            end_date = today
        else:
            if start_date > end_date:
                start_date, end_date = end_date, start_date

            start_date = min(start_date, today)
            end_date = min(end_date, today)

            if start_date > end_date:
                start_date = end_date

            if (end_date - start_date).days > 365:
                start_date = end_date - timedelta(days=365)

    else:
        period = "last_30_days"
        start_date = today - timedelta(days=29)
        end_date = today

    if start_date == end_date:
        period_label = start_date.strftime("%d %B %Y")
    else:
        period_label = (
            f"{start_date.strftime('%d %b %Y')} - "
            f"{end_date.strftime('%d %b %Y')}"
        )

    return period, start_date, end_date, period_label


def _get_logged_in_seller(request):
    seller = get_user_seller(request.user)

    if seller is None:
        logout(request)
        return None

    return seller


def _base_seller_orders(seller):
    return Order.objects.filter(
        seller=seller,
        is_deleted=False,
    )


def _pending_statuses():
    return {
        Order.STATUS_CREATED,
        Order.STATUS_INBOUND,
        Order.STATUS_OUT_FOR_DELIVERY,
        Order.STATUS_PROVINCE_ASSIGNED,
        Order.STATUS_RETURN_ASSIGNED,
        getattr(Order, "STATUS_RETURNING", "RETURNING"),
        getattr(Order, "STATUS_PROCESSING", "PROCESSING"),
    }


def _order_delivery_fee(order):
    return getattr(order, "delivery_fee", ZERO) or ZERO


def _order_additional_fee(order):
    return getattr(order, "additional_fee", ZERO) or ZERO


def _order_province_fee(order):
    return getattr(order, "province_fee", ZERO) or ZERO


def _order_total_fee(order):
    return (
        _order_delivery_fee(order)
        + _order_additional_fee(order)
        + _order_province_fee(order)
    )


def _order_cod(order):
    price = getattr(order, "price", ZERO) or ZERO
    cod = getattr(order, "cod", ZERO) or ZERO
    return price if price != ZERO else cod


def _is_province_order(order):
    status = (getattr(order, "status", "") or "").strip().upper()
    province_fee = _order_province_fee(order)

    return (
        status
        == getattr(
            Order,
            "STATUS_PROVINCE_ASSIGNED",
            "PROVINCE_ASSIGNED",
        )
        or province_fee > ZERO
    )


def _get_created_date(order):
    created_at = getattr(order, "created_at", None)

    if not created_at:
        return None

    try:
        if timezone.is_aware(created_at):
            return timezone.localtime(created_at).date()
    except (TypeError, ValueError):
        pass

    try:
        return created_at.date()
    except (AttributeError, TypeError, ValueError):
        return None


def _get_computer_status(order):
    status = (getattr(order, "status", "") or "").strip().upper()

    if status == getattr(Order, "STATUS_DELIVERED", "DELIVERED"):
        return "delivered", "Delivered"

    if status == getattr(Order, "STATUS_RETURNED", "RETURNED"):
        return "returned", "Returned"

    if status == getattr(Order, "STATUS_VOID", "VOID"):
        return "void", "Void"

    if status == getattr(
        Order,
        "STATUS_PROVINCE_ASSIGNED",
        "PROVINCE_ASSIGNED",
    ):
        return "province", "Province"

    if status in {
        getattr(Order, "STATUS_RETURN_ASSIGNED", "RETURN_ASSIGNED"),
        getattr(Order, "STATUS_RETURNING", "RETURNING"),
    }:
        return "returning", "Returning"

    if status == getattr(
        Order,
        "STATUS_OUT_FOR_DELIVERY",
        "OUT_FOR_DELIVERY",
    ):
        return "pending", "Delivering"

    if status == getattr(Order, "STATUS_INBOUND", "INBOUND"):
        return "pending", "Processing"

    return "pending", "Pending"


def _decorate_order(order):
    status_key, status_label = _get_computer_status(order)

    order.computer_status_key = status_key
    order.computer_status_label = status_label
    order.computer_cod = _order_cod(order)
    order.computer_delivery_fee = _order_delivery_fee(order)
    order.computer_additional_fee = _order_additional_fee(order)
    order.computer_province_fee = _order_province_fee(order)
    order.computer_total_fee = _order_total_fee(order)
    order.computer_fee = order.computer_total_fee
    order.computer_net = order.computer_cod - order.computer_total_fee

    return order


def _common_context(seller, period, start_date, end_date, period_label):
    return {
        "seller": seller,
        "period": period,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "period_label": period_label,
        "today_iso": timezone.localdate().isoformat(),
    }


# =========================================================
# COMPUTER DASHBOARD
# =========================================================

@login_required(login_url="portal:computer_login")
def computer_dashboard(request):
    seller = _get_logged_in_seller(request)

    if seller is None:
        return redirect("portal:computer_login")

    period, start_date, end_date, period_label = _get_report_range(request)
    seller_orders = _base_seller_orders(seller)

    created_orders = list(
        seller_orders.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        ).order_by("created_at", "id")
    )

    delivered_orders = list(
        seller_orders.filter(
            status=Order.STATUS_DELIVERED,
            done_at__gte=start_date,
            done_at__lte=end_date,
        ).order_by("done_at", "id")
    )

    returned_orders = list(
        seller_orders.filter(
            status=Order.STATUS_RETURNED,
            done_at__gte=start_date,
            done_at__lte=end_date,
        ).order_by("done_at", "id")
    )

    pending_statuses = _pending_statuses()
    rows_by_date = {}
    current_date = start_date

    while current_date <= end_date:
        rows_by_date[current_date] = {
            "date": current_date,
            "new_orders": 0,
            "delivered": 0,
            "pending": 0,
            "returned": 0,
            "province": 0,
            "cod": ZERO,
            "fees": ZERO,
            "net": ZERO,
            "delivery_rate": 0,
            "return_rate": 0,
        }
        current_date += timedelta(days=1)

    total_pending = 0
    total_province = 0

    for order in created_orders:
        created_date = _get_created_date(order)

        if created_date not in rows_by_date:
            continue

        row = rows_by_date[created_date]
        row["new_orders"] += 1

        if order.status in pending_statuses:
            row["pending"] += 1
            total_pending += 1

        if _is_province_order(order):
            row["province"] += 1
            total_province += 1

    total_cod = ZERO
    total_fees = ZERO

    for order in delivered_orders:
        done_date = order.done_at

        if done_date not in rows_by_date:
            continue

        row = rows_by_date[done_date]
        cod_value = _order_cod(order)
        fee_value = _order_total_fee(order)

        row["delivered"] += 1
        row["cod"] += cod_value
        row["fees"] += fee_value

        total_cod += cod_value
        total_fees += fee_value

    for order in returned_orders:
        done_date = order.done_at

        if done_date in rows_by_date:
            rows_by_date[done_date]["returned"] += 1

    daily_rows = []

    for row_date in sorted(rows_by_date.keys(), reverse=True):
        row = rows_by_date[row_date]
        row["delivery_rate"] = _safe_rate(
            row["delivered"],
            row["new_orders"],
        )
        row["return_rate"] = _safe_rate(
            row["returned"],
            row["new_orders"],
        )
        row["net"] = row["cod"] - row["fees"]
        daily_rows.append(row)

    total_orders = len(created_orders)
    total_delivered = len(delivered_orders)
    total_returned = len(returned_orders)
    net_balance = total_cod - total_fees

    recent_orders = list(seller_orders.order_by("-id")[:8])

    for order in recent_orders:
        _decorate_order(order)

    cancelled_orders = sum(
        1
        for order in created_orders
        if order.status == Order.STATUS_VOID
    )
    cancellation_rate = _safe_rate(cancelled_orders, total_orders)

    inventory_setting = get_seller_inventory_setting(seller)

    if (
        inventory_setting.stock_mode == InventorySellerSetting.NO_STOCK
        or not inventory_setting.show_stock_in_portal
    ):
        dashboard_stock_rows = []
        automatic_stock_status = "Hidden"
    else:
        dashboard_stock_rows = get_seller_current_stock(seller)
        automatic_stock_status = "Live"

    dashboard_inventory_products = len(dashboard_stock_rows)
    dashboard_available_stock = sum(
        int(row.get("available_qty", 0) or 0)
        for row in dashboard_stock_rows
    )
    dashboard_reserved_stock = sum(
        int(row.get("reserved_qty", 0) or 0)
        for row in dashboard_stock_rows
    )

    context = _common_context(
        seller,
        period,
        start_date,
        end_date,
        period_label,
    )

    context.update(
        {
            "total_orders": total_orders,
            "total_delivered": total_delivered,
            "total_pending": total_pending,
            "total_returned": total_returned,
            "total_province": total_province,
            "total_cod": total_cod,
            "total_fees": total_fees,
            "net_balance": net_balance,
            "delivery_rate": _safe_rate(total_delivered, total_orders),
            "return_rate": _safe_rate(total_returned, total_orders),
            "daily_rows": daily_rows,
            "recent_orders": recent_orders,
            "cancelled_orders": cancelled_orders,
            "cancellation_rate": cancellation_rate,
            "automatic_stock_status": automatic_stock_status,
            "dashboard_inventory_products": dashboard_inventory_products,
            "dashboard_available_stock": dashboard_available_stock,
            "dashboard_reserved_stock": dashboard_reserved_stock,
        }
    )

    return render(
        request,
        "customerportal/computer/dashboard.html",
        context,
    )


# =========================================================
# COMPUTER ORDERS
# =========================================================

@login_required(login_url="portal:computer_login")
def computer_orders(request):
    seller = _get_logged_in_seller(request)

    if seller is None:
        return redirect("portal:computer_login")

    period, start_date, end_date, period_label = _get_report_range(request)
    q = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "ALL").strip().upper()
    pending_statuses = _pending_statuses()

    period_orders = _base_seller_orders(seller).filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )

    summary_total = period_orders.count()
    summary_delivered = period_orders.filter(
        status=Order.STATUS_DELIVERED,
    ).count()
    summary_pending = period_orders.filter(
        status__in=pending_statuses,
    ).count()
    summary_returned = period_orders.filter(
        status=Order.STATUS_RETURNED,
    ).count()

    orders = period_orders

    if q:
        orders = orders.filter(
            Q(tracking_no__icontains=q)
            | Q(seller_order_code__icontains=q)
            | Q(receiver_name__icontains=q)
            | Q(receiver_phone__icontains=q)
            | Q(receiver_address__icontains=q)
            | Q(product_desc__icontains=q)
        )

    if status_filter == "PENDING":
        orders = orders.filter(status__in=pending_statuses)
    elif status_filter == "DELIVERED":
        orders = orders.filter(status=Order.STATUS_DELIVERED)
    elif status_filter == "RETURNED":
        orders = orders.filter(status=Order.STATUS_RETURNED)
    elif status_filter == "PROVINCE":
        orders = orders.filter(
            Q(status=Order.STATUS_PROVINCE_ASSIGNED)
            | Q(province_fee__gt=ZERO)
        )
    elif status_filter == "VOID":
        orders = orders.filter(status=Order.STATUS_VOID)

    orders = list(orders.order_by("-created_at", "-id"))

    for order in orders:
        _decorate_order(order)

    context = _common_context(
        seller,
        period,
        start_date,
        end_date,
        period_label,
    )

    context.update(
        {
            "q": q,
            "status_filter": status_filter,
            "orders": orders,
            "summary_total": summary_total,
            "summary_delivered": summary_delivered,
            "summary_pending": summary_pending,
            "summary_returned": summary_returned,
        }
    )

    return render(
        request,
        "customerportal/computer/orders.html",
        context,
    )


# =========================================================
# COMPUTER DELIVERY REPORT
# =========================================================

@login_required(login_url="portal:computer_login")
def computer_delivery_report(request):
    seller = _get_logged_in_seller(request)

    if seller is None:
        return redirect("portal:computer_login")

    period, start_date, end_date, period_label = _get_report_range(request)
    seller_orders = _base_seller_orders(seller)
    pending_statuses = _pending_statuses()

    created_orders = seller_orders.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )

    completed_rows = list(
        seller_orders.filter(
            status=Order.STATUS_DELIVERED,
            done_at__gte=start_date,
            done_at__lte=end_date,
        ).order_by("-done_at", "-id")
    )

    returned_rows = list(
        seller_orders.filter(
            status=Order.STATUS_RETURNED,
            done_at__gte=start_date,
            done_at__lte=end_date,
        ).order_by("-done_at", "-id")
    )

    for order in completed_rows:
        _decorate_order(order)

    for order in returned_rows:
        _decorate_order(order)

    total_created = created_orders.count()
    total_pending = created_orders.filter(
        status__in=pending_statuses,
    ).count()
    total_delivered = len(completed_rows)
    total_returned = len(returned_rows)

    context = _common_context(
        seller,
        period,
        start_date,
        end_date,
        period_label,
    )

    context.update(
        {
            "total_created": total_created,
            "total_delivered": total_delivered,
            "total_pending": total_pending,
            "total_returned": total_returned,
            "delivery_rate": _safe_rate(total_delivered, total_created),
            "return_rate": _safe_rate(total_returned, total_created),
            "completed_rows": completed_rows,
            "returned_rows": returned_rows,
        }
    )

    return render(
        request,
        "customerportal/computer/delivery_report.html",
        context,
    )


# =========================================================
# COMPUTER COD REPORT
# =========================================================

@login_required(login_url="portal:computer_login")
def computer_cod_report(request):
    seller = _get_logged_in_seller(request)

    if seller is None:
        return redirect("portal:computer_login")

    period, start_date, end_date, period_label = _get_report_range(request)

    rows = list(
        _base_seller_orders(seller).filter(
            status=Order.STATUS_DELIVERED,
            done_at__gte=start_date,
            done_at__lte=end_date,
        ).order_by("-done_at", "-id")
    )

    total_cod = ZERO
    total_delivery_fee = ZERO
    total_additional_fee = ZERO
    total_province_fee = ZERO
    total_fees = ZERO
    net_balance = ZERO

    for order in rows:
        _decorate_order(order)

        total_cod += order.computer_cod
        total_delivery_fee += order.computer_delivery_fee
        total_additional_fee += order.computer_additional_fee
        total_province_fee += order.computer_province_fee
        total_fees += order.computer_total_fee
        net_balance += order.computer_net

    context = _common_context(
        seller,
        period,
        start_date,
        end_date,
        period_label,
    )

    context.update(
        {
            "rows": rows,
            "total_orders": len(rows),
            "total_cod": total_cod,
            "total_delivery_fee": total_delivery_fee,
            "total_additional_fee": total_additional_fee,
            "total_province_fee": total_province_fee,
            "total_fees": total_fees,
            "net_balance": net_balance,
        }
    )

    return render(
        request,
        "customerportal/computer/cod_report.html",
        context,
    )

# =========================================================
# COMPUTER INVENTORY
# =========================================================

@login_required(login_url="portal:computer_login")
def computer_inventory(request):
    seller = _get_logged_in_seller(request)

    if seller is None:
        return redirect("portal:computer_login")

    setting = get_seller_inventory_setting(seller)

    if (
        setting.stock_mode == InventorySellerSetting.NO_STOCK
        or not setting.show_stock_in_portal
    ):
        stock_rows = []
        inventory_enabled = False
    else:
        stock_rows = get_seller_current_stock(seller)
        inventory_enabled = True

    q = (request.GET.get("q") or "").strip().lower()

    if q:
        stock_rows = [
            row
            for row in stock_rows
            if q in str(row.get("sku", "") or "").lower()
            or q in str(row.get("name", "") or "").lower()
            or q in str(row.get("product_type", "") or "").lower()
            or q in str(row.get("location", "") or "").lower()
        ]

    total_products = len(stock_rows)
    total_current = sum(
        int(row.get("current_qty", 0) or 0)
        for row in stock_rows
    )
    total_reserved = sum(
        int(row.get("reserved_qty", 0) or 0)
        for row in stock_rows
    )
    total_available = sum(
        int(row.get("available_qty", 0) or 0)
        for row in stock_rows
    )

    context = {
        "seller": seller,
        "setting": setting,
        "inventory_enabled": inventory_enabled,
        "stock_rows": stock_rows,
        "q": q,
        "total_products": total_products,
        "total_current": total_current,
        "total_reserved": total_reserved,
        "total_available": total_available,
    }

    return render(
        request,
        "customerportal/computer/inventory.html",
        context,
    )

