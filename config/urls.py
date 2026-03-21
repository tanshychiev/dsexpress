from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

from orders import views as order_views
from accounts import views as account_views


urlpatterns = [
    path("admin/", admin.site.urls),

    path("accounts/login/", account_views.staff_login, name="login"),
    path("accounts/logout/", account_views.staff_logout, name="logout"),
    path("accounts/", include("django.contrib.auth.urls")),

    path("", RedirectView.as_view(url="/orders/", permanent=False)),

    path("", include("provinceops.urls")),
    path("", include("returnshop.urls")),
    path("deliver-pp/", include("deliverpp.urls")),

    path("orders/", include("orders.urls")),
    path("", include("masterdata.urls")),
    path("users/", include("accounts.urls")),

    path(
        "api/sellers/autocomplete/",
        order_views.seller_autocomplete,
        name="seller_autocomplete",
    ),

    path("reports/", include("reports.urls")),
    path("portal/", include("customerportal.urls")),
    path("finance/", include("financeops.urls")),
]