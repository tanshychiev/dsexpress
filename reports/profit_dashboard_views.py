from __future__ import annotations

from datetime import datetime, time

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
    today_str = now.strftime("%Y-%m-%d")

    date_from = (request.GET.get("date_from") or today_str).strip()
    date_to = (request.GET.get("date_to") or today_str).strip()
    searched = request.GET.get("search") == "1"

    dashboard = build_profit_dashboard(
        date_from=_parse_date_start(date_from).date() if _parse_date_start(date_from) else now.date(),
        date_to=_parse_date_end(date_to).date() if _parse_date_end(date_to) else now.date(),
    )

    return render(
        request,
        "reports/profit_dashboard.html",
        {
            "searched": searched,
            "date_from": date_from,
            "date_to": date_to,
            "dashboard": dashboard,
        },
    )