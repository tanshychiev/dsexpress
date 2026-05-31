from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils import timezone
from datetime import timedelta

from orders.models import Order
from reports.forms import DeliveryReportFilterForm
from reports.services import (
    get_done_queryset,
    get_pending_queryset,
    group_by_seller,
    calc_totals,
    classify_row,
)
from reports.excel import export_delivery_report_xlsx


@login_required
def seller_report_page(request):
    account = getattr(request.user, "account", None)
    seller_obj = getattr(account, "seller", None)

    if not account or account.account_type != "seller" or not seller_obj:
        return redirect("portal:login")

    # Report date rule:
    # - delivery / done date default = today
    # - pending default = All, but All means last 60 days only
    # - every search is capped inside the last 60 days for both done and pending
    now = timezone.localtime()
    today = now.date()
    limit_start = today - timedelta(days=59)

    today_str = today.strftime("%Y-%m-%d")
    limit_start_str = limit_start.strftime("%Y-%m-%d")

    data = request.GET.copy()

    delivery_all = data.get("delivery_all") == "1"
    pending_all = (
        data.get("pending_all") == "1"
        or (not data.get("pending_date_from") and not data.get("pending_date_to"))
    )

    def clamp_date_value(value, is_to=False):
        if not value:
            return ""

        date_part = str(value)[:10]

        if date_part < limit_start_str:
            date_part = limit_start_str

        if date_part > today_str:
            date_part = today_str

        return f"{date_part}T23:59" if is_to else f"{date_part}T00:00"

    # Delivery: default today, or All = last 60 days.
    if delivery_all:
        data["delivery_date_from"] = f"{limit_start_str}T00:00"
        data["delivery_date_to"] = f"{today_str}T23:59"
    else:
        if not data.get("delivery_date_from"):
            data["delivery_date_from"] = f"{today_str}T00:00"
        else:
            data["delivery_date_from"] = clamp_date_value(data.get("delivery_date_from"), False)

        if not data.get("delivery_date_to"):
            data["delivery_date_to"] = f"{today_str}T23:59"
        else:
            data["delivery_date_to"] = clamp_date_value(data.get("delivery_date_to"), True)

    # Pending: default All = last 60 days only.
    if pending_all:
        data["pending_date_from"] = f"{limit_start_str}T00:00"
        data["pending_date_to"] = f"{today_str}T23:59"
    else:
        if data.get("pending_date_from"):
            data["pending_date_from"] = clamp_date_value(data.get("pending_date_from"), False)

        if data.get("pending_date_to"):
            data["pending_date_to"] = clamp_date_value(data.get("pending_date_to"), True)

    if not data.get("action"):
        data["action"] = "show"

    form = DeliveryReportFilterForm(data)
    action = (data.get("action") or "show").strip()
    show_results = action == "show"

    seller_summaries = []
    grouped = {}

    d_from = None
    d_to = None
    p_from = None
    p_to = None

    top_summary = {
        "total_sent": 0,
        "total_done": 0,
        "total_pending": 0,
        "total_return": 0,
        "total_cod": 0,
        "total_fee": 0,
        "total_pay": 0,
        "total_selected_shops": 0,
    }

    if form.is_valid() and action in ["show", "export"]:
        cleaned = form.cleaned_data.copy()
        cleaned["seller"] = seller_obj

        d_from = cleaned.get("delivery_date_from")
        d_to = cleaned.get("delivery_date_to")
        p_from = cleaned.get("pending_date_from")
        p_to = cleaned.get("pending_date_to")

        done_rows = Order.objects.none()
        pending_rows = Order.objects.none()

        if delivery_all or d_from or d_to:
            done_rows = get_done_queryset(Order, cleaned)

        if pending_all or p_from or p_to:
            pending_rows = get_pending_queryset(Order, cleaned)

        rows = list(done_rows) + list(pending_rows)
        grouped = group_by_seller(rows)

        filtered_rows = []
        total_cod = 0
        total_fee = 0
        total_pay = 0

        for _, seller_rows in grouped.items():
            filtered_rows.extend(seller_rows)

        for seller_key_name, seller_rows in grouped.items():
            seller_total_cod, seller_total_fee, seller_pay = calc_totals(seller_rows)

            total_cod += seller_total_cod
            total_fee += seller_total_fee
            total_pay += seller_pay

            seller_summaries.append({
                "seller_key": seller_key_name,
                "rows": seller_rows,
                "total_cod": seller_total_cod,
                "total_fee": seller_total_fee,
                "pay": seller_pay,
                "total_sent": len(seller_rows),
                "total_done": len([o for o in seller_rows if classify_row(o) == "done"]),
                "total_pending": len([o for o in seller_rows if classify_row(o) == "pending"]),
                "total_return": len([o for o in seller_rows if classify_row(o) == "done_return"]),
            })

        top_summary = {
            "total_sent": len(filtered_rows),
            "total_done": len([o for o in filtered_rows if classify_row(o) == "done"]),
            "total_pending": len([o for o in filtered_rows if classify_row(o) == "pending"]),
            "total_return": len([o for o in filtered_rows if classify_row(o) == "done_return"]),
            "total_cod": total_cod,
            "total_fee": total_fee,
            "total_pay": total_pay,
            "total_selected_shops": len(grouped),
        }

        if action == "export":
            download_name = request.GET.get("download_name", "").strip()
            return export_delivery_report_xlsx(
                grouped,
                "Delivery Report",
                classify_row,
                calc_totals,
                d_from,
                d_to,
                download_name or None,
            )

    return render(request, "customerportal/seller_report.html", {
        "form": form,
        "seller_summaries": seller_summaries,
        "delivery_from": d_from,
        "delivery_to": d_to,
        "pending_from": p_from,
        "pending_to": p_to,
        "top_summary": top_summary,
        "show_results": show_results,
        "seller": seller_obj,
        "default_today": today_str,
        "limit_start": limit_start_str,
        "year_start": limit_start_str,
        "pending_all": pending_all,
        "delivery_all": delivery_all,
    })
