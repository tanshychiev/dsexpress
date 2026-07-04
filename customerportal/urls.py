from django.urls import path

from . import views
from .views_computer import (
    computer_cod_report,
    computer_dashboard,
    computer_delivery_report,
    computer_inventory,
    computer_orders,
    computer_upload_order_delete,
    computer_upload_order_detail,
    computer_upload_orders,
    download_customer_upload_sample,
)
from .views_order_report import seller_order_report
from .views_report import seller_report_page
from .views_shop_roles import (
    shop_role_create,
    shop_role_delete,
    shop_role_duplicate,
    shop_role_edit,
    shop_role_list,
    shop_role_toggle_active,
)
from .views_shop_users import (
    shop_user_archive,
    shop_user_create,
    shop_user_edit,
    shop_user_list,
    shop_user_password,
    shop_user_restore,
    shop_user_toggle_active,
)


app_name = "portal"


urlpatterns = [
    # Public and mobile seller portal
    path("", views.home, name="home"),
    path("tracking/", views.tracking, name="tracking"),
    path("booking/", views.booking_public, name="booking_public"),

    # Seller login pages
    path("login/", views.seller_login, name="login"),
    path(
        "computer/login/",
        views.computer_login,
        name="computer_login",
    ),
    path("logout/", views.seller_logout, name="logout"),

    # Mobile seller portal
    path("dashboard/", views.dashboard, name="dashboard"),
    path("stock/", views.stock, name="stock"),
    path("orders/", seller_order_report, name="orders"),
    path("report/", seller_report_page, name="seller_report"),
    path("cod-report/", views.cod_report, name="cod_report"),

    path(
        "change-password/",
        views.change_password,
        name="change_password",
    ),
    path(
        "booking-seller/",
        views.booking_seller,
        name="booking_seller",
    ),
    path(
        "booking-history/",
        views.booking_history,
        name="booking_history",
    ),
    path(
        "telegram/update-booking/",
        views.telegram_update_booking,
        name="telegram_update_booking",
    ),

    # Computer seller portal
    path(
        "computer/",
        computer_dashboard,
        name="computer_dashboard",
    ),
    path(
        "computer/orders/",
        computer_orders,
        name="computer_orders",
    ),
    path(
        "computer/delivery-report/",
        computer_delivery_report,
        name="computer_delivery_report",
    ),
    path(
        "computer/cod-report/",
        computer_cod_report,
        name="computer_cod_report",
    ),
    path(
        "computer/inventory/",
        computer_inventory,
        name="computer_inventory",
    ),
    path(
        "computer/upload-orders/",
        computer_upload_orders,
        name="computer_upload_orders",
    ),
    path(
        "computer/upload-orders/sample/",
        download_customer_upload_sample,
        name="download_customer_upload_sample",
    ),
    path(
        "computer/upload-orders/<int:batch_id>/",
        computer_upload_order_detail,
        name="computer_upload_order_detail",
    ),
    path(
        "computer/upload-orders/<int:batch_id>/delete/",
        computer_upload_order_delete,
        name="computer_upload_order_delete",
    ),

    # Shop users
    path(
        "computer/shop-users/",
        shop_user_list,
        name="shop_user_list",
    ),
    path(
        "computer/shop-users/create/",
        shop_user_create,
        name="shop_user_create",
    ),
    path(
        "computer/shop-users/<int:account_id>/edit/",
        shop_user_edit,
        name="shop_user_edit",
    ),
    path(
        "computer/shop-users/<int:account_id>/password/",
        shop_user_password,
        name="shop_user_password",
    ),
    path(
        "computer/shop-users/<int:account_id>/toggle-active/",
        shop_user_toggle_active,
        name="shop_user_toggle_active",
    ),
    path(
        "computer/shop-users/<int:account_id>/archive/",
        shop_user_archive,
        name="shop_user_archive",
    ),
    path(
        "computer/shop-users/<int:account_id>/restore/",
        shop_user_restore,
        name="shop_user_restore",
    ),

    # Shop roles
    path(
        "computer/shop-roles/",
        shop_role_list,
        name="shop_role_list",
    ),
    path(
        "computer/shop-roles/create/",
        shop_role_create,
        name="shop_role_create",
    ),
    path(
        "computer/shop-roles/<int:role_id>/edit/",
        shop_role_edit,
        name="shop_role_edit",
    ),
    path(
        "computer/shop-roles/<int:role_id>/duplicate/",
        shop_role_duplicate,
        name="shop_role_duplicate",
    ),
    path(
        "computer/shop-roles/<int:role_id>/toggle-active/",
        shop_role_toggle_active,
        name="shop_role_toggle_active",
    ),
    path(
        "computer/shop-roles/<int:role_id>/delete/",
        shop_role_delete,
        name="shop_role_delete",
    ),
]
