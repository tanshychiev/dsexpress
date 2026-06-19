from django.urls import path

from . import views


app_name = "provincecod"


urlpatterns = [
    path("", views.batch_list, name="batch_list"),
    path("new/", views.batch_create, name="batch_create"),
    path("report/excel/", views.province_cod_report_excel, name="report_excel"),
    path("report/", views.province_cod_report, name="report"),
    path("<int:pk>/", views.batch_detail, name="batch_detail"),
]
