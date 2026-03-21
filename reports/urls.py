from django.urls import path
from .views import (
    delivery_report,
    delivery_report_upload,
    delivery_report_png,
    delivery_report_pdf,
)
from .shipper_cod_views import shipper_cod_report, shipper_cod_report_pdf
from .shipper_commission_views import shipper_commission_report, shipper_commission_report_pdf
from .profit_dashboard_views import profit_dashboard

urlpatterns = [
    # ================= DELIVERY REPORT =================
    path("delivery-report/", delivery_report, name="delivery_report"),
    path("delivery-report/upload/", delivery_report_upload, name="delivery_report_upload"),

    # ✅ IMPORTANT (EXPORT)
    path("delivery-report/png/", delivery_report_png, name="delivery_report_png"),
    path("delivery-report/pdf/", delivery_report_pdf, name="delivery_report_pdf"),

    # ================= SHIPPER COD =================
    path("shipper-cod-report/", shipper_cod_report, name="shipper_cod_report"),
    path("shipper-cod-report/pdf/", shipper_cod_report_pdf, name="shipper_cod_report_pdf"),

    # ================= COMMISSION =================
    path("shipper-commission-report/", shipper_commission_report, name="shipper_commission_report"),
    path("shipper-commission-report/pdf/", shipper_commission_report_pdf, name="shipper_commission_report_pdf"),

    # ================= DASHBOARD =================
    path("profit-dashboard/", profit_dashboard, name="profit_dashboard"),
]