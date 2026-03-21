from django.urls import path
from . import views

urlpatterns = [
    path("", views.user_list, name="user_list"),
    path("create/", views.user_create, name="user_create"),
    path("<int:user_id>/edit/", views.user_edit, name="user_edit"),
    path("<int:user_id>/password/", views.user_change_password, name="user_change_password"),
    path("<int:user_id>/delete/", views.user_delete, name="user_delete"),

    path("roles/", views.role_list, name="role_list"),
    path("roles/create/", views.role_create, name="role_create"),
    path("roles/<int:role_id>/edit/", views.role_edit, name="role_edit"),
    path("roles/<int:role_id>/delete/", views.role_delete, name="role_delete"),
]