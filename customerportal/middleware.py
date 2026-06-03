from django.conf import settings
from django.shortcuts import redirect
from django.utils import timezone

from .models import SellerPortalSession


class SellerPortalActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_portal = request.path.startswith("/portal/")

        if is_portal and request.user.is_authenticated:
            if not request.user.is_staff and hasattr(request.user, "seller_profile"):
                session = (
                    SellerPortalSession.objects
                    .filter(
                        user=request.user,
                        seller=request.user.seller_profile,
                        logout_at__isnull=True,
                    )
                    .order_by("-login_at")
                    .first()
                )

                if session:
                    timeout_seconds = getattr(
                        settings,
                        "SELLER_PORTAL_SESSION_TIMEOUT",
                        60 * 60 * 24 * 180,
                    )

                    now = timezone.now()

                    if session.last_activity_at:
                        inactive_seconds = (now - session.last_activity_at).total_seconds()

                        if inactive_seconds > timeout_seconds:
                            session.logout_at = now
                            session.save(update_fields=["logout_at"])

                            request.session.flush()
                            return redirect("/portal/login/")

                    session.last_activity_at = now
                    session.save(update_fields=["last_activity_at"])

        response = self.get_response(request)
        return response