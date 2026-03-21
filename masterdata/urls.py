from django.urls import path
from . import views

urlpatterns = [
    path("sellers/", views.seller_list, name="seller_list"),
    path("sellers/create/", views.seller_create, name="seller_create"),
    path("sellers/<int:pk>/edit/", views.seller_edit, name="seller_edit"),
    path("sellers/<int:pk>/delete/", views.seller_delete, name="seller_delete"),
    path("sellers/<int:pk>/toggle-active/", views.seller_toggle_active, name="seller_toggle_active"),

    path("shippers/", views.shipper_list, name="shipper_list"),
    path("shippers/create/", views.shipper_create, name="shipper_create"),
    path("shippers/<int:pk>/edit/", views.shipper_edit, name="shipper_edit"),
    path("shippers/<int:pk>/delete/", views.shipper_delete, name="shipper_delete"),
    path("shippers/<int:pk>/toggle-active/", views.shipper_toggle_active, name="shipper_toggle_active"),

    path("api/sellers/autocomplete/", views.seller_autocomplete, name="seller_autocomplete"),
]
