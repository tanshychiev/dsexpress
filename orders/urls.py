from django.urls import path

from . import views
from .views_customer_uploads import (
    staff_customer_upload_approve,
    staff_customer_upload_detail,
    staff_customer_upload_list,
    staff_customer_upload_reject,
)

urlpatterns = [

    # ====================================================
    # ORDER MAIN
    # ====================================================
    path("", views.order_list, name="order_list"),

    # ✅ NEW TOP BUTTON PAGE
    path("receive-invoice/", views.receive_invoice_list, name="receive_invoice_list"),

    path("create/", views.create_order, name="create_order"),
    path("created/<int:pk>/", views.order_created, name="order_created"),
    path("edit/<int:pk>/", views.order_edit, name="order_edit"),
    path("detail/<int:pk>/", views.order_detail, name="order_detail"),
    path("invoice/<int:pk>/", views.order_invoice_detail, name="order_invoice_detail"),
    path("label/<int:pk>/", views.order_label, name="order_label"),

    path("bulk-action/", views.order_bulk_action, name="order_bulk_action"),
    path("batch-print/", views.order_batch_print, name="order_batch_print"),

    path("download-excel/", views.download_orders_excel, name="download_orders_excel"),
    path("api/sellers/autocomplete/", views.seller_autocomplete, name="seller_autocomplete"),

    # ====================================================
    # IMPORT ORDERS
    # ====================================================
    path("import/", views.import_orders, name="import_orders"),

    path(
        "import/batch/<int:batch_id>/",
        views.import_batch_detail,
        name="import_batch_detail",
    ),

    path(
        "import/batch/<int:batch_id>/download/",
        views.download_import_batch_excel,
        name="download_import_batch_excel",
    ),

    path(
        "import/sample-excel/",
        views.download_import_sample_excel,
        name="download_import_sample_excel",
    ),

    path(
        "import/batch/<int:batch_id>/delete/",
        views.delete_import_batch,
        name="delete_import_batch",
    ),

    # ====================================================
    # CUSTOMER UPLOAD APPROVAL - STAFF INTERNAL SYSTEM
    # URL: /orders/customer-uploads/
    # ====================================================
    path(
        "customer-uploads/",
        staff_customer_upload_list,
        name="staff_customer_upload_list",
    ),
    path(
        "customer-uploads/<int:batch_id>/",
        staff_customer_upload_detail,
        name="staff_customer_upload_detail",
    ),
    path(
        "customer-uploads/<int:batch_id>/approve/",
        staff_customer_upload_approve,
        name="staff_customer_upload_approve",
    ),
    path(
        "customer-uploads/<int:batch_id>/reject/",
        staff_customer_upload_reject,
        name="staff_customer_upload_reject",
    ),

    # ====================================================
    # BULK UPDATE
    # ====================================================
    path("update/", views.bulk_update, name="bulk_update"),

    path(
        "update/upload/",
        views.bulk_update,
        name="bulk_update_upload",
    ),

    path(
        "update/template/",
        views.download_update_template,
        name="download_update_template",
    ),

    path(
        "update/batch/<int:batch_id>/",
        views.bulk_update_batch_detail,
        name="bulk_update_batch_detail",
    ),

    path(
        "update/batch/<int:batch_id>/download/",
        views.download_bulk_update_batch_excel,
        name="download_bulk_update_batch_excel",
    ),

    # legacy routes old templates compatibility
    path("upload-update/", views.bulk_update, name="upload_update"),

    path(
        "update-template/",
        views.download_update_template,
        name="download_update_template_legacy",
    ),

    # ====================================================
    # TRASH SYSTEM
    # ====================================================
    path("trash/", views.order_trash, name="order_trash"),

    path(
        "<int:pk>/delete/",
        views.order_delete,
        name="order_delete",
    ),

    path(
        "<int:pk>/restore/",
        views.order_restore,
        name="order_restore",
    ),

    # ====================================================
    # AUDIT LOG PAGE
    # ====================================================
    path(
        "audit-logs/",
        views.audit_log_list,
        name="audit_log_list",
    ),
]