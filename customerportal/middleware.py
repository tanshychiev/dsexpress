from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.shortcuts import redirect
from django.utils import timezone

from .models import (
    SellerPortalDailyUsage,
    SellerPortalPageUsage,
    SellerPortalSession,
)


class SellerPortalActivityMiddleware:
    """
    Tracks seller portal sessions safely for the owner and all sub-users.

    It uses request.user.account.seller, which is the relationship already used
    by the seller portal. A maximum five-minute gap is counted as active time;
    longer gaps are treated as idle time rather than usage.
    """

    ACTIVE_GAP_LIMIT_SECONDS = 5 * 60

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _get_seller_account(user):
        account = getattr(user, "account", None)
        if not account:
            return None, None

        if getattr(account, "account_type", "") != "seller":
            return account, None

        return account, getattr(account, "seller", None)

    @staticmethod
    def _page_details(request):
        match = getattr(request, "resolver_match", None)
        page_key = ""
        page_name = ""

        if match:
            page_key = (getattr(match, "view_name", "") or "").strip()
            page_name = (getattr(match, "url_name", "") or "").replace("_", " ").title()

        if not page_key:
            page_key = (request.path or "/portal/")[:150]

        if not page_name:
            page_name = page_key.replace("portal:", "").replace("_", " ").title()[:180]

        return page_key[:150], page_name[:180]

    def __call__(self, request):
        path = request.path or "/"
        is_portal = path.startswith("/portal/")

        if is_portal and request.user.is_authenticated and not request.user.is_staff:
            account, seller = self._get_seller_account(request.user)

            # A seller user must be linked to an active seller and not archived.
            if (
                not account
                or not seller
                or not seller.is_active
                or getattr(account, "is_archived", False)
            ):
                request.session.flush()
                return redirect("/portal/login/")

            now = timezone.now()
            page_key, page_name = self._page_details(request)

            with transaction.atomic():
                session = (
                    SellerPortalSession.objects.select_for_update()
                    .filter(
                        user=request.user,
                        seller=seller,
                        logout_at__isnull=True,
                    )
                    .order_by("-login_at")
                    .first()
                )

                # Create a session if an old login exists without a session row.
                if session is None:
                    session = SellerPortalSession.objects.create(
                        seller=seller,
                        user=request.user,
                        login_at=now,
                        last_activity_at=now,
                        last_page_key=page_key,
                        last_page_name=page_name,
                        ip_address=request.META.get("REMOTE_ADDR", "")[:100],
                        user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
                    )

                timeout_seconds = getattr(
                    settings,
                    "SELLER_PORTAL_SESSION_TIMEOUT",
                    60 * 60 * 24 * 180,
                )

                previous_activity = session.last_activity_at
                inactive_seconds = 0

                if previous_activity:
                    inactive_seconds = max(
                        int((now - previous_activity).total_seconds()),
                        0,
                    )

                if inactive_seconds > timeout_seconds:
                    session.logout_at = now
                    session.save(update_fields=["logout_at"])
                    request.session.flush()
                    return redirect("/portal/login/")

                # Only short gaps count as actual usage. This avoids counting a
                # browser left open for many hours as active work.
                active_delta = (
                    inactive_seconds
                    if 0 < inactive_seconds <= self.ACTIVE_GAP_LIMIT_SECONDS
                    else 0
                )

                usage_date = timezone.localdate(now)
                daily, _ = SellerPortalDailyUsage.objects.get_or_create(
                    seller=seller,
                    user=request.user,
                    usage_date=usage_date,
                    defaults={
                        "first_seen_at": now,
                        "last_seen_at": now,
                    },
                )

                SellerPortalDailyUsage.objects.filter(pk=daily.pk).update(
                    active_seconds=F("active_seconds") + active_delta,
                    page_views=F("page_views") + 1,
                    last_seen_at=now,
                )

                # Attribute elapsed time to the page that was open before this
                # request, then count this request as a view of the current page.
                if active_delta and session.last_page_key:
                    previous_page, _ = SellerPortalPageUsage.objects.get_or_create(
                        daily_usage=daily,
                        page_key=session.last_page_key,
                        defaults={"page_name": session.last_page_name},
                    )
                    SellerPortalPageUsage.objects.filter(pk=previous_page.pk).update(
                        active_seconds=F("active_seconds") + active_delta,
                    )

                current_page, _ = SellerPortalPageUsage.objects.get_or_create(
                    daily_usage=daily,
                    page_key=page_key,
                    defaults={"page_name": page_name},
                )
                SellerPortalPageUsage.objects.filter(pk=current_page.pk).update(
                    page_views=F("page_views") + 1,
                    page_name=page_name,
                )

                session.last_activity_at = now
                session.last_page_key = page_key
                session.last_page_name = page_name
                session.active_seconds = (session.active_seconds or 0) + active_delta
                session.save(
                    update_fields=[
                        "last_activity_at",
                        "last_page_key",
                        "last_page_name",
                        "active_seconds",
                    ]
                )

        return self.get_response(request)
