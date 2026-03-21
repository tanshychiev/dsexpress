from django.urls import path

from . import views
from . import clearpp_views

urlpatterns = [
    # DeliverPP
    path("", views.deliverpp_list, name="deliverpp_list"),
    path("new/", views.pp_delivery_create, name="deliverpp_new"),
    path("<int:batch_id>/", views.pp_delivery_detail, name="deliverpp_detail"),
    path("<int:batch_id>/print/", views.pp_delivery_print, name="deliverpp_print"),

    # Clear PP
    path("clear/", clearpp_views.clearpp_list, name="clearpp_list"),
    path("clear/settings/", clearpp_views.system_settings_view, name="clearpp_settings"),
    path("clear/<int:batch_id>/", clearpp_views.clearpp_detail, name="clearpp_detail"),

    # ✅ MUST MATCH FUNCTION NAMES IN clearpp_views.py
    path("clear/<int:batch_id>/toggle-tick/", clearpp_views.clearpp_toggle_tick, name="clearpp_toggle_tick"),
    path("clear/<int:batch_id>/set-tick-many/", clearpp_views.clearpp_set_tick_many, name="clearpp_set_tick_many"),

    path("clear/<int:batch_id>/clear-delivery/", clearpp_views.clear_delivery_ajax, name="clearpp_clear_delivery"),
    path("clear/<int:batch_id>/undo/", clearpp_views.clearpp_undo_clear, name="clearpp_undo_clear"),
    path("clear/<int:batch_id>/cancel/", clearpp_views.clearpp_cancel, name="clearpp_cancel"),
]