from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    # Inventory main pages
    path("", views.inventory_list, name="list"),
    path("stock-in/", views.stock_in, name="stock_in"),
    path("adjust/", views.adjust_stock_view, name="adjust"),
    path("confirm/", views.confirm_stock_view, name="confirm"),
    path("history/", views.history, name="history"),

    # Product edit: edit inventory product name / code / SKU / photo / location
    path(
        "products/<int:product_id>/edit/",
        views.product_edit,
        name="product_edit",
    ),

    # Seller stock setting: STRICT / OPTIONAL / NO_STOCK
    path(
        "settings/<int:seller_id>/",
        views.seller_inventory_setting,
        name="seller_setting",
    ),

    # Fix stock for one order after import/create/edit
    path(
        "orders/<int:order_id>/choose-stock/",
        views.choose_order_stock,
        name="choose_order_stock",
    ),

    # API for create/edit order goods popup
    path(
        "api/products/",
        views.stock_products_api,
        name="stock_products_api",
    ),
]