from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from customerportal.models import SellerUploadBatch, SellerUploadRow
from orders.activity import add_order_activity
from orders.audit import add_audit_log
from orders.models import AuditLog, ImportBatch, Order, OrderActivity
from orders.pricing import apply_pricing


# ============================================================
# STAFF INTERNAL - CUSTOMER UPLOAD APPROVAL
# URL: /orders/customer-uploads/
# ============================================================

def _staff_upload_required(request):
    return bool(
        request.user.is_authenticated
        and request.user.is_staff
    )


def _decorate_upload_rows_product_display(seller, rows):
    """
    Staff display helper. Product Description is customer free text;
    Inventory Product is looked up from SKU for readable verification.
    """
    try:
        from inventory.models import StockProduct
        from inventory.services import match_product
    except Exception:
        StockProduct = None
        match_product = None

    for row in rows:
        input_description = (getattr(row, "product_name_input", "") or "").strip()
        input_sku = (getattr(row, "sku_input", "") or "").strip()
        matched_name = (getattr(row, "matched_product_name", "") or "").strip()
        matched_sku = (getattr(row, "matched_sku", "") or "").strip()
        old_product_text = (getattr(row, "product_desc", "") or "").strip()

        product = None

        if StockProduct:
            for val in [matched_sku, input_sku, old_product_text]:
                if not val:
                    continue
                product = (
                    StockProduct.objects
                    .filter(seller=seller, is_active=True, sku__iexact=val)
                    .first()
                )
                if product:
                    break

        if not product and match_product:
            for val in [matched_name, old_product_text]:
                if not val:
                    continue
                product = match_product(seller, val)
                if product:
                    break

        if product:
            row.display_inventory_product = product.name or "-"
            row.display_inventory_sku = product.sku or "-"
        else:
            row.display_inventory_product = matched_name or "-"
            row.display_inventory_sku = matched_sku or input_sku or "-"

        if input_description:
            row.display_product_description = input_description
        elif product and old_product_text and old_product_text.upper() == (getattr(product, "sku", "") or "").upper():
            row.display_product_description = "-"
        else:
            row.display_product_description = old_product_text or "-"

        row.display_uploaded_sku = input_sku or matched_sku or "-"


def _make_customer_upload_tracking_no():
    today = timezone.localdate()
    date_str = today.strftime("%Y%m%d")
    prefix = f"DS{date_str}"

    last_order = (
        Order.objects
        .select_for_update()
        .filter(tracking_no__startswith=prefix)
        .exclude(tracking_no__startswith="TEMP-")
        .order_by("-tracking_no")
        .first()
    )

    next_seq = 1

    if last_order and last_order.tracking_no:
        try:
            next_seq = int(last_order.tracking_no[len(prefix):]) + 1
        except Exception:
            next_seq = 1

    return f"{prefix}{next_seq:04d}"


def _recalc_upload_batch(batch):
    rows = batch.rows.all()

    batch.total_rows = rows.count()
    batch.valid_rows = rows.filter(status=SellerUploadRow.STATUS_VALID).count()
    batch.error_rows = rows.filter(status=SellerUploadRow.STATUS_ERROR).count()
    batch.duplicate_rows = rows.filter(status=SellerUploadRow.STATUS_DUPLICATE).count()

    batch.save(
        update_fields=[
            "total_rows",
            "valid_rows",
            "error_rows",
            "duplicate_rows",
            "updated_at",
        ]
    )


@login_required
def staff_customer_upload_list(request):
    if not _staff_upload_required(request):
        messages.error(request, "Staff only.")
        return redirect("login")

    status = (request.GET.get("status") or "PENDING").strip().upper()

    batches = SellerUploadBatch.objects.select_related(
        "seller",
        "uploaded_by",
        "approved_by",
        "rejected_by",
    ).order_by("-id")

    if status != "ALL":
        batches = batches.filter(status=status)

    pending_count = SellerUploadBatch.objects.filter(
        status=SellerUploadBatch.STATUS_PENDING,
    ).count()

    batches = list(batches[:200])
    for batch in batches:
        batch.display_upload_remark = (batch.upload_remark or "").strip() or "-"
        batch.display_filename = (batch.original_filename or "").strip() or "-"

    return render(
        request,
        "orders/customer_uploads/list.html",
        {
            "batches": batches,
            "status": status,
            "pending_count": pending_count,
        },
    )


@login_required
def staff_customer_upload_detail(request, batch_id):
    if not _staff_upload_required(request):
        messages.error(request, "Staff only.")
        return redirect("login")

    batch = get_object_or_404(
        SellerUploadBatch.objects.select_related(
            "seller",
            "uploaded_by",
            "approved_by",
            "rejected_by",
        ),
        id=batch_id,
    )

    rows = list(batch.rows.all().order_by("row_number", "id"))
    _decorate_upload_rows_product_display(batch.seller, rows)

    return render(
        request,
        "orders/customer_uploads/detail.html",
        {
            "batch": batch,
            "rows": rows,
        },
    )


@login_required
def staff_customer_upload_reject(request, batch_id):
    if not _staff_upload_required(request):
        messages.error(request, "Staff only.")
        return redirect("login")

    batch = get_object_or_404(SellerUploadBatch, id=batch_id)

    if request.method != "POST":
        return redirect("staff_customer_upload_detail", batch_id=batch.id)

    if batch.status not in [
        SellerUploadBatch.STATUS_PENDING,
        SellerUploadBatch.STATUS_APPROVED,
    ]:
        messages.error(request, "This upload cannot be rejected now.")
        return redirect("staff_customer_upload_detail", batch_id=batch.id)

    reason = (request.POST.get("reject_reason") or "").strip()

    if not reason:
        messages.error(request, "Please enter reject reason.")
        return redirect("staff_customer_upload_detail", batch_id=batch.id)

    batch.status = SellerUploadBatch.STATUS_REJECTED
    batch.reject_reason = reason or "Rejected by staff."
    batch.rejected_by = request.user
    batch.rejected_at = timezone.now()
    batch.save()

    messages.success(request, "Upload rejected.")
    return redirect("staff_customer_upload_detail", batch_id=batch.id)


@login_required
def staff_customer_upload_approve(request, batch_id):
    if not _staff_upload_required(request):
        messages.error(request, "Staff only.")
        return redirect("login")

    batch = get_object_or_404(SellerUploadBatch, id=batch_id)

    if request.method != "POST":
        return redirect("staff_customer_upload_detail", batch_id=batch.id)

    if batch.status != SellerUploadBatch.STATUS_PENDING:
        messages.error(request, "Only pending uploads can be approved.")
        return redirect("staff_customer_upload_detail", batch_id=batch.id)

    _recalc_upload_batch(batch)

    if batch.error_rows > 0 or batch.duplicate_rows > 0 or batch.valid_rows <= 0:
        messages.error(request, "Cannot approve. Please reject or fix errors first.")
        return redirect("staff_customer_upload_detail", batch_id=batch.id)

    try:
        with transaction.atomic():
            batch = SellerUploadBatch.objects.select_for_update().get(id=batch.id)

            if batch.status != SellerUploadBatch.STATUS_PENDING:
                messages.error(request, "This upload was already processed.")
                return redirect("staff_customer_upload_detail", batch_id=batch.id)

            rows = list(
                batch.rows.select_for_update().filter(
                    status=SellerUploadRow.STATUS_VALID,
                    imported_order__isnull=True,
                ).order_by("row_number", "id")
            )

            if not rows:
                messages.error(request, "No valid rows to import.")
                return redirect("staff_customer_upload_detail", batch_id=batch.id)

            duplicate_errors = []

            for row in rows:
                if Order.objects.filter(
                    seller=batch.seller,
                    seller_order_code__iexact=row.seller_order_code,
                    is_deleted=False,
                ).exists():
                    row.status = SellerUploadRow.STATUS_DUPLICATE
                    row.error_message = "Seller Order Code already exists before approval."
                    row.save(update_fields=["status", "error_message"])
                    duplicate_errors.append(row.row_number)

            if duplicate_errors:
                _recalc_upload_batch(batch)
                messages.error(
                    request,
                    "Cannot approve. Some seller order codes became duplicate. Please check rows.",
                )
                return redirect("staff_customer_upload_detail", batch_id=batch.id)

            import_batch = ImportBatch.objects.create(
                filename=f"customer_upload_{batch.code}_{batch.original_filename}"
            )

            imported_count = 0

            for row in rows:
                order = Order.objects.create(
                    tracking_no=_make_customer_upload_tracking_no(),
                    seller=batch.seller,
                    seller_code=batch.seller.code or "",
                    seller_name=row.seller_name or batch.seller.name or "",
                    seller_order_code=row.seller_order_code,
                    product_desc=row.product_desc,
                    quantity=row.quantity,
                    cod=row.cod,
                    price=row.price,
                    receiver_name=row.receiver_name,
                    receiver_phone=row.receiver_phone,
                    receiver_address=row.receiver_address,
                    remark=row.remark,
                    import_batch=import_batch,
                    status=Order.STATUS_CREATED,
                    updated_by=request.user,
                )

                apply_pricing(order)
                order.save()

                try:
                    from inventory.models import StockProduct
                    from inventory.services import set_order_stock, auto_link_order_stock

                    upload_sku = (row.matched_sku or row.sku_input or "").strip()
                    stock_product = None
                    if upload_sku:
                        stock_product = (
                            StockProduct.objects
                            .filter(seller=batch.seller, is_active=True, sku__iexact=upload_sku)
                            .first()
                        )

                    if stock_product:
                        set_order_stock(
                            order=order,
                            product=stock_product,
                            qty=order.quantity,
                            raw_text=upload_sku,
                            actor=request.user,
                            note=f"Linked stock from customer upload SKU {upload_sku}",
                        )
                    else:
                        auto_link_order_stock(order, actor=request.user)
                except Exception:
                    pass

                add_order_activity(
                    order=order,
                    action=OrderActivity.ACTION_CREATE,
                    user=request.user,
                    old_status="",
                    new_status=order.status,
                    note=f"Created from customer upload {batch.code}",
                )

                try:
                    add_audit_log(
                        module=AuditLog.MODULE_ORDER,
                        obj=order,
                        action=getattr(AuditLog, "ACTION_CREATE", "CREATE"),
                        user=request.user,
                        field_name="customer_upload",
                        old_value="",
                        new_value=batch.code,
                        note=f"Order created after staff approved customer upload {batch.code}",
                    )
                except Exception:
                    pass

                row.imported_order = order
                row.save(update_fields=["imported_order"])

                imported_count += 1

            now = timezone.now()

            batch.status = SellerUploadBatch.STATUS_IMPORTED
            batch.approved_by = request.user
            batch.approved_at = now
            batch.imported_at = now
            batch.imported_count = imported_count
            batch.save()

    except Exception as e:
        messages.error(request, f"Import failed: {e}")
        return redirect("staff_customer_upload_detail", batch_id=batch.id)

    messages.success(request, f"Approved and imported {batch.imported_count} orders.")
    return redirect("staff_customer_upload_detail", batch_id=batch.id)
