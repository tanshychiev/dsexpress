from datetime import date
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from deliverpp.models import Order


def _get_logged_in_seller(request):
    if not request.user.is_authenticated:
        return None
    if not hasattr(request.user, "seller_profile"):
        return None
    seller = request.user.seller_profile
    if not seller.is_active:
        return None
    return seller


def convert_status(status):
    processing_status = [
        "CREATED",
        "INBOUND",
        "DELIVERING",
        "ASSIGNING",
        "PROCESSING",
        "RETURNING",
    ]
    done_status = [
        "DELIVERED",
        "DONE",
    ]
    return_status = [
        "RETURNED",
        "DONE_RETURN",
    ]

    if status in processing_status:
        return "PROCESSING"
    if status in done_status:
        return "DONE"
    if status in return_status:
        return "RETURN"
    return "PROCESSING"


@login_required
def seller_order_report(request):
    seller = _get_logged_in_seller(request)
    if seller is None:
        return redirect("portal:login")

    today = date.today().strftime("%Y-%m-%d")

    date_from = request.GET.get("from") or today
    date_to = request.GET.get("to") or today
    status_filter = request.GET.get("status", "ALL")

    has_searched = "search" in request.GET

    orders = Order.objects.filter(seller=seller)

    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)

    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)

    orders = orders.order_by("-id")

    total_sent = 0
    total_processing = 0
    total_done = 0
    total_return = 0
    order_list = []

    for o in orders:
        main_status = convert_status(o.status)

        total_sent += 1
        if main_status == "PROCESSING":
            total_processing += 1
        elif main_status == "DONE":
            total_done += 1
        elif main_status == "RETURN":
            total_return += 1

        if has_searched:
            if status_filter != "ALL" and main_status != status_filter:
                continue
            o.main_status = main_status
            order_list.append(o)

    if total_sent > 0:
        total_done_pct = int((total_done / total_sent) * 100)
        total_processing_pct = int((total_processing / total_sent) * 100)
        total_return_pct = int((total_return / total_sent) * 100)
    else:
        total_done_pct = 0
        total_processing_pct = 0
        total_return_pct = 0

    return render(
        request,
        "customerportal/orders.html",
        {
            "orders": order_list,
            "total_sent": total_sent,
            "total_processing": total_processing,
            "total_done": total_done,
            "total_return": total_return,
            "total_done_pct": total_done_pct,
            "total_processing_pct": total_processing_pct,
            "total_return_pct": total_return_pct,
            "status_filter": status_filter,
            "date_from": date_from,
            "date_to": date_to,
            "seller": seller,
            "has_searched": has_searched,
        },
    )