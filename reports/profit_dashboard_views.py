from __future__ import annotations

from datetime import datetime, time, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from .profit_dashboard_services import build_profit_dashboard


def _parse_date_start(value: str):
    if not value:
        return None

    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
        dt = datetime.combine(d, time.min)
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except Exception:
        return None


def _parse_date_end(value: str):
    if not value:
        return None

    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
        dt = datetime.combine(d, time.max)
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except Exception:
        return None


@login_required
def profit_dashboard(request):
    now = timezone.localtime()
    today = now.date()
    today_str = today.strftime("%Y-%m-%d")

    date_from = (request.GET.get("date_from") or today_str).strip()
    date_to = (request.GET.get("date_to") or today_str).strip()
    searched = request.GET.get("search") == "1"

    parsed_from = _parse_date_start(date_from)
    parsed_to = _parse_date_end(date_to)

    final_date_from = parsed_from.date() if parsed_from else today
    final_date_to = parsed_to.date() if parsed_to else today

    # Separate carrier report dates from the main dashboard date filter.
    default_carrier_to = today
    default_carrier_from = today - timedelta(days=29)
    raw_carrier_from = (request.GET.get("carrier_from") or "").strip()
    raw_carrier_to = (request.GET.get("carrier_to") or "").strip()
    parsed_carrier_from = _parse_date_start(raw_carrier_from) if raw_carrier_from else None
    parsed_carrier_to = _parse_date_start(raw_carrier_to) if raw_carrier_to else None
    carrier_from = parsed_carrier_from.date() if parsed_carrier_from else default_carrier_from
    carrier_to = parsed_carrier_to.date() if parsed_carrier_to else default_carrier_to
    carrier_date_error = ""

    if raw_carrier_from or raw_carrier_to:
        if not parsed_carrier_from or not parsed_carrier_to:
            carrier_date_error = "Select valid From and To dates."
        elif carrier_from > carrier_to:
            carrier_date_error = "From date must be on or before To date."
        elif (carrier_to - carrier_from).days > 366:
            carrier_date_error = "Choose a date range of 367 days or fewer."

    if carrier_date_error:
        carrier_from, carrier_to = default_carrier_from, default_carrier_to

    dashboard = build_profit_dashboard(
        date_from=final_date_from,
        date_to=final_date_to,
        carrier_date_from=carrier_from,
        carrier_date_to=carrier_to,
    )

    return render(
        request,
        "reports/profit_dashboard.html",
        {
            "searched": searched,
            "date_from": date_from,
            "date_to": date_to,
            "dashboard": dashboard,
            "carrier_from": carrier_from.isoformat(),
            "carrier_to": carrier_to.isoformat(),
            "carrier_date_error": carrier_date_error,
        },
    )