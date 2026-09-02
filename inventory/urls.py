from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    # Inventory main pages
    path("", views.inventory_list, name="list"),
    path("stock-in/", views.stock_in, name="stock_in"),
    path("stock-in/list/", views.stock_in_list, name="stock_in_list"),
    path("stock-in/receipt/<str:batch_ref>/", views.stock_in_receipt, name="stock_in_receipt"),
    path("adjust/", views.adjust_stock_view, name="adjust"),
    path("confirm/", views.confirm_stock_view, name="confirm"),
    path("history/", views.history, name="history"),

    # Product edit
    path(
        "products/<int:product_id>/edit/",
        views.product_edit,
        name="product_edit",
    ),

    # Seller stock setting
    path(
        "settings/<int:seller_id>/",
        views.seller_inventory_setting,
        name="seller_setting",
    ),

    # Fix stock for one order
    path(
        "orders/<int:order_id>/choose-stock/",
        views.choose_order_stock,
        name="choose_order_stock",
    ),

    # API
    path(
        "api/products/",
        views.stock_products_api,
        name="stock_products_api",
    ),

    path("customer-stock-png/", views.customer_stock_png, name="customer_stock_png"),
]
