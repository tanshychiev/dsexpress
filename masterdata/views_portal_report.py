from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import (
    BigIntegerField,
    Count,
    IntegerField,
    Max,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from accounts.models import Account
from customerportal.models import (
    SellerPortalAuditLog,
    SellerPortalDailyUsage,
    SellerPortalPageUsage,
    SellerPortalSession,
)
from masterdata.models import Seller


def _seconds_to_minutes(seconds):
    return max(int((seconds or 0) // 60), 0)


def _report_range(request):
    today = timezone.localdate()
    period = (request.GET.get("period") or "this_month").strip().lower()

    if period == "today":
        start_date = today
        end_date = today
    elif period == "last_7_days":
        start_date = today - timedelta(days=6)
        end_date = today
    elif period == "last_30_days":
        start_date = today - timedelta(days=29)
        end_date = today
    elif period == "custom":
        try:
            start_date = date.fromisoformat(
                (request.GET.get("from") or "").strip()
            )
            end_date = date.fromisoformat(
                (request.GET.get("to") or "").strip()
            )
        except (TypeError, ValueError):
            period = "this_month"
            start_date = today.replace(day=1)
            end_date = today
    else:
        period = "this_month"
        start_date = today.replace(day=1)
        end_date = today

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    if (end_date - start_date).days > 366:
        start_date = end_date - timedelta(days=366)

    return period, start_date, end_date


def _account_summary(seller):
    online_cutoff = timezone.now() - timedelta(minutes=5)

    accounts = list(
        Account.objects.filter(
            seller=seller,
            account_type=Account.ACCOUNT_TYPE_SELLER,
        ).select_related("user")
    )

    active_additional_count = 0

    for account in accounts:
        is_owner = bool(
            account.is_seller_owner
            or seller.portal_user_id == account.user_id
        )

        if (
            not is_owner
            and not account.is_archived
            and account.user.is_active
        ):
            active_additional_count += 1

    online_count = (
        SellerPortalSession.objects.filter(
            seller=seller,
            logout_at__isnull=True,
            last_activity_at__gte=online_cutoff,
        )
        .values("user_id")
        .distinct()
        .count()
    )

    return {
        "total_linked_accounts": len(accounts),
        "active_additional_count": active_additional_count,
        "online_count": online_count,
    }


@login_required
def seller_portal_report(request, pk: int):
    seller = get_object_or_404(
        Seller.objects.select_related("portal_user"),
        pk=pk,
    )

    period, start_date, end_date = _report_range(request)

    usage_qs = (
        SellerPortalDailyUsage.objects
        .filter(
            seller=seller,
            usage_date__gte=start_date,
            usage_date__lte=end_date,
        )
        .select_related("user")
    )

    totals = usage_qs.aggregate(
        active_seconds=Coalesce(
            Sum("active_seconds"),
            Value(0),
            output_field=BigIntegerField(),
        ),
        page_views=Coalesce(
            Sum("page_views"),
            Value(0),
            output_field=IntegerField(),
        ),
        active_users=Count("user", distinct=True),
        active_days=Count("usage_date", distinct=True),
        first_seen_at=Max("first_seen_at"),
        last_seen_at=Max("last_seen_at"),
    )
    totals["active_minutes"] = _seconds_to_minutes(
        totals["active_seconds"]
    )
    totals["active_hours"] = round(
        (totals["active_seconds"] or 0) / 3600,
        2,
    )

    user_rows = list(
        usage_qs.values(
            "user_id",
            "user__username",
            "user__first_name",
            "user__last_name",
        )
        .annotate(
            active_seconds=Coalesce(
                Sum("active_seconds"),
                Value(0),
                output_field=BigIntegerField(),
            ),
            page_views=Coalesce(
                Sum("page_views"),
                Value(0),
                output_field=IntegerField(),
            ),
            active_days=Count("usage_date", distinct=True),
            last_seen_at=Max("last_seen_at"),
        )
        .order_by("-active_seconds", "user__username")
    )

    for item in user_rows:
        item["active_minutes"] = _seconds_to_minutes(
            item["active_seconds"]
        )
        item["active_hours"] = round(
            (item["active_seconds"] or 0) / 3600,
            2,
        )
        full_name = (
            f'{item["user__first_name"]} '
            f'{item["user__last_name"]}'
        ).strip()
        item["display_name"] = full_name or item["user__username"]

    daily_rows = list(
        usage_qs.values("usage_date")
        .annotate(
            active_seconds=Coalesce(
                Sum("active_seconds"),
                Value(0),
                output_field=BigIntegerField(),
            ),
            page_views=Coalesce(
                Sum("page_views"),
                Value(0),
                output_field=IntegerField(),
            ),
            active_users=Count("user", distinct=True),
        )
        .order_by("-usage_date")
    )

    for item in daily_rows:
        item["active_minutes"] = _seconds_to_minutes(
            item["active_seconds"]
        )

    top_pages = list(
        SellerPortalPageUsage.objects
        .filter(
            daily_usage__seller=seller,
            daily_usage__usage_date__gte=start_date,
            daily_usage__usage_date__lte=end_date,
        )
        .values("page_key", "page_name")
        .annotate(
            active_seconds=Coalesce(
                Sum("active_seconds"),
                Value(0),
                output_field=BigIntegerField(),
            ),
            page_views=Coalesce(
                Sum("page_views"),
                Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("-active_seconds", "-page_views")[:30]
    )

    for item in top_pages:
        item["active_minutes"] = _seconds_to_minutes(
            item["active_seconds"]
        )

    sessions = list(
        SellerPortalSession.objects
        .filter(
            seller=seller,
            login_at__date__gte=start_date,
            login_at__date__lte=end_date,
        )
        .select_related("user")
        .order_by("-login_at")[:100]
    )

    audit_logs = list(
        SellerPortalAuditLog.objects
        .filter(
            seller=seller,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        .select_related("performed_by", "target_user")
        .order_by("-created_at")[:100]
    )

    return render(
        request,
        "masterdata/seller_portal_report.html",
        {
            "row": seller,
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "totals": totals,
            "user_rows": user_rows,
            "daily_rows": daily_rows,
            "top_pages": top_pages,
            "sessions": sessions,
            "audit_logs": audit_logs,
            "account_summary": _account_summary(seller),
        },
    )
