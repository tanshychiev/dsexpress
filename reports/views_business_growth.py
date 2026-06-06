from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from .business_growth_services import (
    DEFAULT_EXPECTED_GROWTH_RATE,
    DEFAULT_MONTHLY_GOAL,
    DEFAULT_MONTHS,
    build_business_growth_tracker,
)


def _parse_int(value, default, min_value=None, max_value=None):
    try:
        result = int(value)
    except Exception:
        result = default

    if min_value is not None:
        result = max(result, min_value)

    if max_value is not None:
        result = min(result, max_value)

    return result


def _parse_float(value, default, min_value=None, max_value=None):
    try:
        result = float(value)
    except Exception:
        result = default

    if min_value is not None:
        result = max(result, min_value)

    if max_value is not None:
        result = min(result, max_value)

    return result


@login_required
def business_growth_tracker(request):
    today = timezone.localdate()

    months = _parse_int(
        request.GET.get("months"),
        DEFAULT_MONTHS,
        min_value=3,
        max_value=36,
    )

    monthly_goal = _parse_int(
        request.GET.get("monthly_goal"),
        DEFAULT_MONTHLY_GOAL,
        min_value=0,
        max_value=9999999,
    )

    expected_growth_rate = _parse_float(
        request.GET.get("expected_growth_rate"),
        DEFAULT_EXPECTED_GROWTH_RATE,
        min_value=-100,
        max_value=1000,
    )

    growth = build_business_growth_tracker(
        as_of_date=today,
        months_count=months,
        monthly_goal=monthly_goal,
        expected_growth_rate=expected_growth_rate,
    )

    return render(
        request,
        "reports/business_growth_tracker.html",
        {
            "growth": growth,
            "months": months,
            "monthly_goal": monthly_goal,
            "expected_growth_rate": expected_growth_rate,
        },
    )