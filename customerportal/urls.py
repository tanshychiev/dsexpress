from django.urls import path
from . import views
from .views_order_report import seller_order_report
from .views_report import seller_report_page

app_name = "portal"

urlpatterns = [
    path("", views.home, name="home"),
    path("tracking/", views.tracking, name="tracking"),
    path("booking/", views.booking_public, name="booking_public"),
    path("login/", views.seller_login, name="login"),
    path("logout/", views.seller_logout, name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("orders/", seller_order_report, name="orders"),
    path("report/", seller_report_page, name="seller_report"),
    path("cod-report/", views.cod_report, name="cod_report"),
    path("change-password/", views.change_password, name="change_password"),
    path("booking-seller/", views.booking_seller, name="booking_seller"),
    path("booking-history/", views.booking_history, name="booking_history"),
    path("telegram/update-booking/", views.telegram_update_booking, name="telegram_update_booking"),
]