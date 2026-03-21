from django.urls import path
from . import views

urlpatterns = [
    path("province/", views.province_list, name="province_list"),
    path("province/new/", views.province_new, name="province_new"),
    path("province/<int:pk>/", views.province_detail, name="province_detail"),
    path("province/<int:pk>/print/", views.province_print, name="province_print"),
]
