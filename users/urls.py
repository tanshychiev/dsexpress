from django.urls import path
from . import views

urlpatterns = [
    path("", views.user_list, name="user_list"),

    path("create/", views.user_create, name="user_create"),

    # IMPORTANT: put EDIT before <int:pk>/password/
    path("<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("<int:pk>/password/", views.user_password, name="user_password"),
    path("<int:pk>/toggle-active/", views.user_toggle_active, name="user_toggle_active"),

    # Roles (Groups)
    path("roles/", views.role_list, name="role_list"),
    path("roles/create/", views.role_create, name="role_create"),
    path("roles/<int:pk>/edit/", views.role_edit, name="role_edit"),
    path("roles/<int:pk>/delete/", views.role_delete, name="role_delete"),
]
