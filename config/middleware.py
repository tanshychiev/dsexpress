from django.shortcuts import redirect
from django.urls import resolve, Resolver404


class InternalLoginRequiredMiddleware:
    ALLOWED_PREFIXES = (
        "/accounts/login/",
        "/accounts/logout/",
        "/portal/",
        "/admin/",
        "/static/",
        "/media/",
        "/reports/delivery-report/png/",
        "/reports/delivery-report/pdf/",
    )

    ALLOWED_URL_NAMES = {
        "login",
        "logout",
        "delivery_report_png",
        "delivery_report_pdf",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or "/"

        for prefix in self.ALLOWED_PREFIXES:
            if path.startswith(prefix):
                return self.get_response(request)

        if path == "/favicon.ico":
            return self.get_response(request)

        try:
            match = resolve(path)
            url_name = match.url_name
        except Resolver404:
            url_name = None

        if url_name in self.ALLOWED_URL_NAMES:
            return self.get_response(request)

        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")

        if not request.user.is_staff:
            return redirect("/portal/login/")

        return self.get_response(request)