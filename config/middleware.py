from difflib import SequenceMatcher

from django.shortcuts import redirect
from django.urls import resolve, Resolver404


class InternalLoginRequiredMiddleware:
    LOGIN_URL = "/accounts/login/"
    PORTAL_HOME = "/portal/"
    PORTAL_LOGIN = "/portal/login/"

    # Change this if your internal dashboard is not "/"
    INTERNAL_HOME = "/"

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

    ALLOWED_EXACT_PATHS = {
        "/favicon.ico",
        "/robots.txt",
        "/portal",
    }

    # iPhone / PWA may request these from root.
    # Do NOT let these redirect to /portal/ as HTML.
    PWA_ROOT_REDIRECTS = {
        "/apple-touch-icon.png": "/static/img/favicon.png?v=20260604ds10",
        "/apple-touch-icon-precomposed.png": "/static/img/favicon.png?v=20260604ds10",
        "/apple-touch-icon-180x180.png": "/static/img/favicon.png?v=20260604ds10",
        "/manifest.json": "/static/img/manifest/ds-express.webmanifest?v=20260604ds10",
        "/site.webmanifest": "/static/img/manifest/ds-express.webmanifest?v=20260604ds10",
    }

    ALLOWED_URL_NAMES = {
        "login",
        "logout",
        "delivery_report_png",
        "delivery_report_pdf",
    }

    PORTAL_WORDS = (
        "portal",
        "seller",
        "shop",
        "customer",
        "tracking",
        "track",
        "booking",
        "book",
    )

    INTERNAL_WORDS = (
        "admin",
        "account",
        "accounts",
        "dashboard",
        "order",
        "orders",
        "delivery",
        "deliver",
        "deliverpp",
        "province",
        "return",
        "reports",
        "report",
        "inventory",
        "stock",
        "cod",
        "masterdata",
        "shipper",
        "seller-list",
        "users",
        "staff",
    )

    FILE_EXTENSIONS = (
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".svg",
        ".ico",
        ".css",
        ".js",
        ".map",
        ".json",
        ".webmanifest",
        ".txt",
        ".xml",
        ".woff",
        ".woff2",
        ".ttf",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def _first_segment(self, path):
        clean = (path or "/").strip("/").lower()
        if not clean:
            return ""
        return clean.split("/")[0]

    def _similarity(self, a, b):
        return SequenceMatcher(None, a, b).ratio()

    def _looks_like_words(self, segment, words):
        if not segment:
            return False

        segment = segment.lower()

        for word in words:
            if segment == word:
                return True

            # Example: por -> portal
            if len(segment) >= 3 and word.startswith(segment):
                return True

            # Example: portl -> portal
            if self._similarity(segment, word) >= 0.72:
                return True

        return False

    def _looks_like_file_request(self, path):
        clean = (path or "").lower().split("?")[0]
        return clean.endswith(self.FILE_EXTENSIONS)

    def _go_internal(self, request):
        if not request.user.is_authenticated:
            return redirect(f"{self.LOGIN_URL}?next={self.INTERNAL_HOME}")

        if not request.user.is_staff:
            return redirect(self.PORTAL_LOGIN)

        return redirect(self.INTERNAL_HOME)

    def __call__(self, request):
        path = request.path or "/"

        # Fix /portal without slash
        if path == "/portal":
            return redirect(self.PORTAL_HOME)

        # Important for iPhone Add to Home Screen icon / manifest
        if path in self.PWA_ROOT_REDIRECTS:
            return redirect(self.PWA_ROOT_REDIRECTS[path])

        # Public exact paths
        if path in self.ALLOWED_EXACT_PATHS:
            return self.get_response(request)

        # Public prefixes
        for prefix in self.ALLOWED_PREFIXES:
            if path.startswith(prefix):
                return self.get_response(request)

        # If browser requests a file, do not redirect to portal HTML.
        # Let Django/nginx return normal 404 if the file does not exist.
        if self._looks_like_file_request(path):
            return self.get_response(request)

        # Try normal Django URL first
        try:
            match = resolve(path)
            url_name = match.url_name
        except Resolver404:
            segment = self._first_segment(path)

            is_portal = self._looks_like_words(segment, self.PORTAL_WORDS)
            is_internal = self._looks_like_words(segment, self.INTERNAL_WORDS)

            if is_internal and not is_portal:
                return self._go_internal(request)

            if is_portal and not is_internal:
                return redirect(self.PORTAL_HOME)

            # Unclear wrong URL:
            # staff -> internal, normal customer/visitor -> portal
            if request.user.is_authenticated and request.user.is_staff:
                return redirect(self.INTERNAL_HOME)

            return redirect(self.PORTAL_HOME)

        # Allow by URL name
        if url_name in self.ALLOWED_URL_NAMES:
            return self.get_response(request)

        # Real internal page needs login
        if not request.user.is_authenticated:
            return redirect(f"{self.LOGIN_URL}?next={request.path}")

        # Logged in seller/customer cannot enter internal system
        if not request.user.is_staff:
            return redirect(self.PORTAL_LOGIN)

        return self.get_response(request)