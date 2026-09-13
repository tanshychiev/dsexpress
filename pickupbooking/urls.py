from django.urls import path

from . import views

app_name = "pickupbooking"

urlpatterns = [
    path("", views.pickup_booking, name="home"),
    path("history/", views.pickup_history, name="history"),
    path("history/<int:pk>/edit/", views.pickup_edit, name="edit"),
]
