from django.utils import timezone
from .models import SellerPortalSession


class SellerPortalActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.path.startswith("/portal/") and request.user.is_authenticated:
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
                    session.last_activity_at = timezone.now()
                    session.save(update_fields=["last_activity_at"])

        return response