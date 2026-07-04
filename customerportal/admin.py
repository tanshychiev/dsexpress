from django.contrib import admin

from .models import (
    SellerBooking,
    SellerPortalSession,
    SellerUploadBatch,
    SellerUploadRow,
)


@admin.register(SellerBooking)
class SellerBookingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "seller",
        "sender_phone",
        "total_pc",
        "status",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "seller__name",
        "seller__code",
        "sender_phone",
        "sender_address",
    )


@admin.register(SellerPortalSession)
class SellerPortalSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "seller",
        "user",
        "login_at",
        "logout_at",
        "last_activity_at",
        "session_minutes",
        "ip_address",
    )
    list_filter = (
        "login_at",
        "logout_at",
        "seller",
    )
    search_fields = (
        "seller__name",
        "seller__code",
        "user__username",
        "ip_address",
    )
    readonly_fields = (
        "seller",
        "user",
        "login_at",
        "logout_at",
        "last_activity_at",
        "ip_address",
        "user_agent",
    )

    def session_minutes(self, obj):
        return obj.duration_minutes
    session_minutes.short_description = "Minutes"


class SellerUploadRowInline(admin.TabularInline):
    model = SellerUploadRow
    extra = 0
    readonly_fields = (
        "row_number",
        "seller_order_code",
        "seller_name",
        "product_name_input",
        "sku_input",
        "matched_product_name",
        "matched_sku",
        "receiver_name",
        "receiver_phone",
        "receiver_address",
        "product_desc",
        "quantity",
        "cod",
        "price",
        "remark",
        "status",
        "error_message",
        "imported_order",
        "created_at",
    )
    can_delete = False


@admin.register(SellerUploadBatch)
class SellerUploadBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "seller",
        "status",
        "short_upload_remark",
        "total_rows",
        "valid_rows",
        "error_rows",
        "duplicate_rows",
        "imported_count",
        "uploaded_by",
        "approved_by",
        "created_at",
    )
    list_filter = (
        "status",
        "created_at",
        "seller",
    )
    search_fields = (
        "seller__name",
        "seller__code",
        "original_filename",
        "upload_remark",
        "uploaded_by__username",
    )
    readonly_fields = (
        "seller",
        "uploaded_by",
        "file",
        "original_filename",
        "upload_remark",
        "status",
        "short_upload_remark",
        "total_rows",
        "valid_rows",
        "error_rows",
        "duplicate_rows",
        "imported_count",
        "reject_reason",
        "approved_by",
        "approved_at",
        "rejected_by",
        "rejected_at",
        "imported_at",
        "created_at",
        "updated_at",
    )
    inlines = [SellerUploadRowInline]

    def code(self, obj):
        return obj.code

    def short_upload_remark(self, obj):
        text = (obj.upload_remark or "").strip()
        return text[:60] + ("..." if len(text) > 60 else "")
    short_upload_remark.short_description = "Upload Remark"


@admin.register(SellerUploadRow)
class SellerUploadRowAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "batch",
        "row_number",
        "seller_order_code",
        "seller_name",
        "product_name_input",
        "sku_input",
        "matched_product_name",
        "matched_sku",
        "receiver_name",
        "receiver_phone",
        "cod",
        "status",
        "imported_order",
    )
    list_filter = (
        "status",
        "created_at",
    )
    search_fields = (
        "batch__seller__name",
        "batch__seller__code",
        "seller_order_code",
        "seller_name",
        "product_name_input",
        "sku_input",
        "matched_product_name",
        "matched_sku",
        "receiver_name",
        "receiver_phone",
        "receiver_address",
    )
    readonly_fields = (
        "batch",
        "row_number",
        "seller_order_code",
        "seller_name",
        "product_name_input",
        "sku_input",
        "matched_product_name",
        "matched_sku",
        "receiver_name",
        "receiver_phone",
        "receiver_address",
        "product_desc",
        "quantity",
        "cod",
        "price",
        "remark",
        "status",
        "error_message",
        "imported_order",
        "created_at",
    )
