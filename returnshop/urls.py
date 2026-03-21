from django.urls import path
from . import views

urlpatterns = [
    path("return-shop/", views.returnshop_list, name="returnshop_list"),
    path("return-shop/new/", views.returnshop_new, name="returnshop_new"),
    path("return-shop/<int:pk>/", views.returnshop_detail, name="returnshop_detail"),

    path("return-shop/history/", views.returnshop_history, name="returnshop_history"),

    path("return-shop/<int:pk>/labels/", views.returnshop_labels, name="returnshop_labels"),

    path("return-shop/label/<int:pk>/", views.returnshop_label_detail, name="returnshop_label_detail"),
    path("return-shop/label/<int:pk>/print/", views.returnshop_label_print, name="returnshop_label_print"),
]
