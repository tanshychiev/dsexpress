from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import (
    SellerPortalDailyUsage,
    SellerPortalPageUsage,
    SellerPortalSession,
)
from .permissions import (
    get_seller_account,
    user_has_portal_permission,
)


class SellerPortalActivityMiddleware:
    """
    Tracks seller portal usage and blocks seller users from opening pages
    that their seller role does not allow.

    Seller lookup uses request.user.account.seller, so it works for the main
    owner and every additional shop user.
    """

    ACTIVE_GAP_LIMIT_SECONDS = 5 * 60

    VIEW_PERMISSION_MAP = {
        "portal:dashboard": "dashboard.view",
        "portal:computer_dashboard": "dashboard.view",

        "portal:orders": "orders.view",
        "portal:computer_orders": "orders.view",

        "portal:seller_report": "delivery_report.view",
        "portal:computer_delivery_report": "delivery_report.view",
        "portal:cod_report": "cod_report.view",
        "portal:computer_cod_report": "cod_report.view",

        "portal:stock": "inventory.view",
        "portal:computer_inventory": "inventory.view",

        "portal:shop_user_list": "users.manage",
        "portal:shop_user_create": "users.manage",
        "portal:shop_user_edit": "users.manage",
        "portal:shop_user_toggle_active": "users.manage",
        "portal:shop_user_archive": "users.manage",
        "portal:shop_user_restore": "users.manage",
        "portal:shop_user_password": "users.reset_password",

        "portal:shop_role_list": "roles.manage",
        "portal:shop_role_create": "roles.manage",
        "portal:shop_role_edit": "roles.manage",
        "portal:shop_role_duplicate": "roles.manage",
        "portal:shop_role_toggle_active": "roles.manage",
        "portal:shop_role_delete": "roles.manage",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _page_details(request):
        match = getattr(request, "resolver_match", None)
        page_key = ""
        page_name = ""

        if match:
            page_key = (
                getattr(match, "view_name", "")
                or ""
            ).strip()

            page_name = (
                getattr(match, "url_name", "")
                or ""
            ).replace("_", " ").title()

        if not page_key:
            page_key = (request.path or "/portal/")[:150]

        if not page_name:
            page_name = (
                page_key
                .replace("portal:", "")
                .replace("_", " ")
                .title()
            )[:180]

        return page_key[:150], page_name[:180]

    def process_view(
        self,
        request,
        view_func,
        view_args,
        view_kwargs,
    ):
        """
        Backend permission enforcement.

        Menu hiding is only visual. This method also blocks direct URLs.
        """
        path = request.path or ""

        if not path.startswith("/portal/"):
            return None

        if not request.user.is_authenticated:
            return None

        if request.user.is_staff:
            return None

        account = get_seller_account(request.user)

        if not account:
            request.session.flush()
            return redirect("/portal/login/")

        match = getattr(request, "resolver_match", None)
        view_name = getattr(match, "view_name", "") if match else ""
        permission_key = self.VIEW_PERMISSION_MAP.get(view_name)

        if not permission_key:
            return None

        if user_has_portal_permission(
            request.user,
            permission_key,
        ):
            return None

        if path.startswith("/portal/computer/"):
            return render(
                request,
                "customerportal/computer/permission_denied.html",
                {
                    "seller": account.seller,
                    "permission_key": permission_key,
                },
                status=403,
            )

        messages.error(
            request,
            "You do not have permission to open that page.",
        )
        return redirect("portal:dashboard")

    def _check_timeout(self, request, seller):
        session = (
            SellerPortalSession.objects
            .filter(
                user=request.user,
                seller=seller,
                logout_at__isnull=True,
            )
            .order_by("-login_at")
            .first()
        )

        if not session or not session.last_activity_at:
            return None

        timeout_seconds = getattr(
            settings,
            "SELLER_PORTAL_SESSION_TIMEOUT",
            60 * 60 * 24 * 180,
        )

        inactive_seconds = (
            timezone.now() - session.last_activity_at
        ).total_seconds()

        if inactive_seconds <= timeout_seconds:
            return None

        session.logout_at = timezone.now()
        session.save(update_fields=["logout_at"])
        request.session.flush()
        return redirect("/portal/login/")

    def _record_activity(self, request, seller):
        now = timezone.now()
        page_key, page_name = self._page_details(request)

        with transaction.atomic():
            session = (
                SellerPortalSession.objects
                .select_for_update()
                .filter(
                    user=request.user,
                    seller=seller,
                    logout_at__isnull=True,
                )
                .order_by("-login_at")
                .first()
            )

            if session is None:
                session = SellerPortalSession.objects.create(
                    seller=seller,
                    user=request.user,
                    login_at=now,
                    last_activity_at=now,
                    last_page_key=page_key,
                    last_page_name=page_name,
                    ip_address=(
                        request.META.get("REMOTE_ADDR", "")
                        or ""
                    )[:100],
                    user_agent=(
                        request.META.get(
                            "HTTP_USER_AGENT",
                            "",
                        )
                        or ""
                    )[:1000],
                )

            previous_activity = session.last_activity_at
            inactive_seconds = 0

            if previous_activity:
                inactive_seconds = max(
                    int(
                        (
                            now - previous_activity
                        ).total_seconds()
                    ),
                    0,
                )

            active_delta = (
                inactive_seconds
                if (
                    0 < inactive_seconds
                    <= self.ACTIVE_GAP_LIMIT_SECONDS
                )
                else 0
            )

            usage_date = timezone.localdate(now)

            daily, _ = (
                SellerPortalDailyUsage.objects
                .get_or_create(
                    seller=seller,
                    user=request.user,
                    usage_date=usage_date,
                    defaults={
                        "first_seen_at": now,
                        "last_seen_at": now,
                    },
                )
            )

            SellerPortalDailyUsage.objects.filter(
                pk=daily.pk,
            ).update(
                active_seconds=(
                    F("active_seconds") + active_delta
                ),
                page_views=F("page_views") + 1,
                last_seen_at=now,
            )

            if active_delta and session.last_page_key:
                previous_page, _ = (
                    SellerPortalPageUsage.objects
                    .get_or_create(
                        daily_usage=daily,
                        page_key=session.last_page_key,
                        defaults={
                            "page_name": session.last_page_name,
                        },
                    )
                )

                SellerPortalPageUsage.objects.filter(
                    pk=previous_page.pk,
                ).update(
                    active_seconds=(
                        F("active_seconds") + active_delta
                    ),
                )

            current_page, _ = (
                SellerPortalPageUsage.objects
                .get_or_create(
                    daily_usage=daily,
                    page_key=page_key,
                    defaults={"page_name": page_name},
                )
            )

            SellerPortalPageUsage.objects.filter(
                pk=current_page.pk,
            ).update(
                page_views=F("page_views") + 1,
                page_name=page_name,
            )

            session.last_activity_at = now
            session.last_page_key = page_key
            session.last_page_name = page_name
            session.active_seconds = (
                session.active_seconds or 0
            ) + active_delta

            session.save(
                update_fields=[
                    "last_activity_at",
                    "last_page_key",
                    "last_page_name",
                    "active_seconds",
                ]
            )

    def __call__(self, request):
        path = request.path or "/"
        is_portal = path.startswith("/portal/")
        seller = None

        if (
            is_portal
            and request.user.is_authenticated
            and not request.user.is_staff
        ):
            account = get_seller_account(request.user)

            if not account:
                request.session.flush()
                return redirect("/portal/login/")

            seller = account.seller
            timeout_response = self._check_timeout(
                request,
                seller,
            )

            if timeout_response:
                return timeout_response

        response = self.get_response(request)

        if (
            seller
            and response.status_code < 400
            and request.user.is_authenticated
        ):
            self._record_activity(request, seller)

        return response
