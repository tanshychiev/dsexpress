from __future__ import annotations

import base64
from collections import defaultdict
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from orders.models import Order
from .models import ReturnShopBatch, ReturnShopBatchItem, ReturnShopLabel, ReturnShopLabelItem


# =========================
# Helpers
# =========================
def _scan_key_new():
    return "returnshop_new_scan_codes"


def _scan_key_new_text():
    return "returnshop_new_scan_text"


def _scan_key_edit_text(pk: int):
    return f"returnshop_edit_scan_text_{pk}"


def _parse_codes(raw: str):
    out, seen = [], set()
    for line in (raw or "").splitlines():
        c = (line or "").strip()
        if c and c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _get_orders_by_tracking(codes):
    qs = Order.objects.filter(tracking_no__in=codes, is_deleted=False).select_related("seller")
    m = {o.tracking_no: o for o in qs}

    found, notfound = [], 0
    for c in codes:
        o = m.get(c)
        if not o:
            notfound += 1
            continue
        found.append(o)
    return found, notfound


def _get_field(obj, names, default=""):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v not in ("", None):
                return v
    return default


def _order_status(o: Order) -> str:
    return str(getattr(o, "status", "") or "").upper()


def _display_status(st: str) -> str:
    """
    Preview should show REAL order status.
    Only map PROCESSING -> RETURNING for Return module display.
    """
    st = (st or "").upper()
    if st == "PROCESSING":
        return "RETURNING"
    return st


def _status_returning() -> str:
    """
    Internal Order status used for Returning.
    We store PROCESSING in DB, but display as RETURNING.
    """
    return "PROCESSING"


def _status_returned() -> str:
    """Internal Order status used for Returned."""
    return "RETURNED"


def _can_assign_status(st: str) -> bool:
    """
    Allow these to be included in Return-To-Shop batch (preview + save):
    - INBOUND, CREATED (normal)
    - DONE / DELIVERED (rare edge case: cash closed but goods remain)
    - RETURNED (allowed to preview)
    """
    st = (st or "").upper()
    return st in ("INBOUND", "CREATED", "DONE", "DELIVERED", "RETURNED")


def _set_cod(order: Order, value):
    if hasattr(order, "cod"):
        order.cod = value
        order.save(update_fields=["cod"])


def _set_order_status(order: Order, status: str):
    if hasattr(order, "status"):
        order.status = status
        order.save(update_fields=["status"])


def _receiver_location(o: Order) -> str:
    v = _get_field(
        o,
        ["receiver_location", "receiver_address", "address", "district", "city", "province"],
        "",
    )
    return str(v) if v else "-"


def _customer_phone(o: Order) -> str:
    v = _get_field(o, ["customer_phone", "receiver_phone", "phone", "tel"], "")
    return str(v) if v else "-"


def _customer_name(o: Order) -> str:
    v = _get_field(o, ["customer_name", "receiver_name", "name"], "")
    return str(v) if v else "-"


def _price(o: Order):
    v = _get_field(o, ["price", "total_price", "amount"], 0)
    return v or 0


def _cod(o: Order):
    v = getattr(o, "cod", 0)
    return 0 if v is None else v


def _order_detail_url(o: Order) -> str:
    return f"/orders/created/{o.id}/"


def _seller_name(o: Order) -> str:
    s = getattr(o, "seller", None)
    return getattr(s, "name", "-") or "-"


def _label_code(batch_id: int, label_id: int) -> str:
    return f"RTS-{batch_id}-{label_id}"


def _qr_data_uri(text: str) -> str:
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=1,
    )
    qr.add_data(text)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white").resize((360, 360))
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return "data:image/png;base64," + b64


def _batch_status_cancelled():
    return getattr(ReturnShopBatch, "STATUS_CANCELLED", "CANCELLED")


def _batch_status_done():
    return getattr(ReturnShopBatch, "STATUS_DONE", "DONE")


def _batch_status_pending():
    return getattr(ReturnShopBatch, "STATUS_PENDING", "PENDING")


def _as_decimal(value: str) -> Decimal:
    try:
        return Decimal(str(value or "0").strip())
    except Exception:
        return Decimal("0.00")


# ============================================================
# Progress helpers
# ============================================================
def _get_done_label_codes_from_pp(label_codes):
    codes = [str(x).strip() for x in (label_codes or []) if str(x).strip()]
    if not codes:
        return set()

    # TODO: connect to real Deliver PP done/ticked rows
    return set()


def _build_batch_progress_map(batches):
    batches = list(batches)
    if not batches:
        return {}

    all_codes = []
    for batch in batches:
        labels_qs = getattr(batch, "_prefetched_objects_cache", {}).get("labels")
        if labels_qs is None:
            labels_qs = batch.labels.all()
        for lb in labels_qs:
            code = (getattr(lb, "code", "") or "").strip()
            if code:
                all_codes.append(code)

    done_codes = _get_done_label_codes_from_pp(all_codes)

    progress_map = {}
    for batch in batches:
        counts = batch.get_progress_counts(done_codes=done_codes)
        progress_map[batch.id] = {
            "total_count": counts["total_count"],
            "done_count": counts["done_count"],
            "pending_count": counts["pending_count"],
            "label": batch.get_progress_label(done_codes=done_codes),
        }
    return progress_map


# =========================
# LIST (Batch list)
# =========================
@login_required
def returnshop_list(request):
    status = (request.GET.get("status") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    searched = request.GET.get("search") == "1"

    qs = (
        ReturnShopBatch.objects
        .select_related("created_by")
        .prefetch_related(
            Prefetch(
                "labels",
                queryset=ReturnShopLabel.objects.only("id", "batch_id", "code").order_by("id"),
            )
        )
        .order_by("-id")
    )

    if status:
        qs = qs.filter(status=status.upper())
    if date_from:
        qs = qs.filter(assigned_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(assigned_at__date__lte=date_to)

    qs = qs.annotate(
        total_pc=Count("items", distinct=True),
        total_labels=Count("labels", distinct=True),
    )

    batch_rows = list(qs) if searched else []

    rows = []
    for batch in batch_rows:
        total_labels = getattr(batch, "total_labels", 0) or 0

        if batch.status == _batch_status_cancelled():
            progress_label = "CANCELLED"
        elif total_labels <= 0:
            progress_label = "PENDING"
        else:
            progress_label = f"{total_labels}/{total_labels}"

        rows.append(
            {
                "id": batch.id,
                "assigned_at": batch.assigned_at,
                "created_by": batch.created_by,
                "total_pc": getattr(batch, "total_pc", 0),
                "total_labels": total_labels,
                "progress_label": progress_label,
                "raw_status": batch.status,
            }
        )

    return render(
        request,
        "returnshop/returnshop_list.html",
        {
            "rows": rows,
            "searched": searched,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


# =========================
# HISTORY (Label archive)
# =========================
@login_required
def returnshop_history(request):
    q = (request.GET.get("q") or "").strip()

    if q:
        hit = ReturnShopLabel.objects.filter(code=q).first()
        if hit:
            return redirect("returnshop_label_detail", pk=hit.id)

    labels_qs = (
        ReturnShopLabel.objects
        .select_related("batch", "batch__created_by")
        .prefetch_related(
            Prefetch(
                "batch__labels",
                queryset=ReturnShopLabel.objects.only("id", "batch_id", "code").order_by("id"),
            )
        )
        .order_by("-id")
    )

    if q:
        labels_qs = labels_qs.filter(code__icontains=q)

    label_rows = list(labels_qs[:500])

    batch_map = {}
    for lb in label_rows:
        if lb.batch_id and lb.batch_id not in batch_map:
            batch_map[lb.batch_id] = lb.batch

    progress_map = _build_batch_progress_map(batch_map.values())

    rows = []
    for lb in label_rows:
        total_pc = ReturnShopLabelItem.objects.filter(label=lb).count()

        seller_set = set()
        li_qs = ReturnShopLabelItem.objects.filter(label=lb).select_related("batch_item__order__seller")
        for li in li_qs:
            seller_set.add(_seller_name(li.batch_item.order))
        shop_names = " + ".join(sorted(seller_set)) if seller_set else "-"

        created_at = getattr(lb, "created_at", None) or (lb.batch.assigned_at if lb.batch else None)
        batch_progress = progress_map.get(lb.batch_id or 0, {})
        progress_label = batch_progress.get("label", lb.batch.status if lb.batch else "-")

        rows.append(
            {
                "id": lb.id,
                "code": lb.code,
                "shop_names": shop_names,
                "total_pc": total_pc,
                "created_at": created_at,
                "status": progress_label,
            }
        )

    return render(request, "returnshop/returnshop_history.html", {"q": q, "rows": rows})


# =========================
# NEW (SCAN / CREATE Batch)
# =========================
@login_required
def returnshop_new(request):
    codes_key = _scan_key_new()
    text_key = _scan_key_new_text()

    scanned_codes = request.session.get(codes_key, [])
    scan_value = request.session.get(text_key, "")

    found_orders, notfound = _get_orders_by_tracking(scanned_codes)

    allowed_orders, error_orders = [], []
    for o in found_orders:
        if _can_assign_status(_order_status(o)):
            allowed_orders.append(o)
        else:
            error_orders.append(o)

    if request.method == "POST":
        action = request.POST.get("action") or ""
        posted_scan = request.POST.get("scan_codes", "") or ""

        request.session[text_key] = posted_scan
        request.session.modified = True

        if action == "scan_add":
            codes = _parse_codes(posted_scan)
            if not codes:
                messages.error(request, "Please scan tracking code(s).")
                return redirect("returnshop_new")

            existing = request.session.get(codes_key, [])
            seen = set(existing)
            added = 0
            for c in codes:
                if c not in seen:
                    existing.append(c)
                    seen.add(c)
                    added += 1

            request.session[codes_key] = existing
            request.session.modified = True
            messages.success(request, f"Added {added} code(s).")
            return redirect("returnshop_new")

        if action == "scan_clear":
            request.session[codes_key] = []
            request.session[text_key] = ""
            request.session.modified = True
            messages.success(request, "Cleared scanned list.")
            return redirect("returnshop_new")

        if action == "confirm_create":
            remark = (request.POST.get("remark") or "").strip()
            mode = (request.POST.get("mode") or "save").strip()

            checked_ids = request.POST.getlist("checked_ids")
            checked_ids = [int(x) for x in checked_ids if str(x).isdigit()]
            if not checked_ids:
                messages.error(request, "Please tick at least 1 shipment.")
                return redirect("returnshop_new")

            allowed_map = {o.id: o for o in allowed_orders}
            selected_orders = [allowed_map.get(i) for i in checked_ids if allowed_map.get(i)]
            if not selected_orders:
                messages.error(request, "Selected shipments are not allowed. Remove red rows.")
                return redirect("returnshop_new")

            with transaction.atomic():
                batch = ReturnShopBatch.objects.create(
                    created_by=request.user,
                    assigned_at=timezone.now(),
                    remark=remark,
                    status=_batch_status_pending(),
                )

                for o in selected_orders:
                    ReturnShopBatchItem.objects.create(
                        batch=batch,
                        order=o,
                        cod_before=_cod(o),
                        status_before=_order_status(o),
                    )
                    _set_cod(o, 0)
                    _set_order_status(o, _status_returning())

                request.session[codes_key] = []
                request.session.modified = True

                if mode == "complete":
                    batch.status = _batch_status_done()
                    batch.save(update_fields=["status"])
                    for o in selected_orders:
                        _set_cod(o, 0)
                        _set_order_status(o, _status_returned())
                    messages.success(request, "Return to Shop batch created and completed.")
                    return redirect("returnshop_detail", pk=batch.id)

                if mode == "print_label":
                    messages.success(request, "Batch created. Now create label batches.")
                    return redirect("returnshop_labels", pk=batch.id)

            messages.success(request, "Batch created.")
            return redirect("returnshop_detail", pk=batch.id)

    rows_error = [
        {
            "id": o.id,
            "tracking_no": getattr(o, "tracking_no", "-"),
            "tracking_url": _order_detail_url(o),
            "seller_name": _seller_name(o),
            "receiver_location": _receiver_location(o),
            "customer_name": _customer_name(o),
            "customer_phone": _customer_phone(o),
            "price": _price(o),
            "cod": _cod(o),
            "status": _display_status(_order_status(o)) or "-",
        }
        for o in error_orders
    ]

    rows_allowed = [
        {
            "id": o.id,
            "tracking_no": getattr(o, "tracking_no", "-"),
            "tracking_url": _order_detail_url(o),
            "seller_name": _seller_name(o),
            "receiver_location": _receiver_location(o),
            "customer_name": _customer_name(o),
            "customer_phone": _customer_phone(o),
            "price": _price(o),
            "cod": _cod(o),
            "status": _display_status(_order_status(o)) or "-",
            "is_done": _order_status(o) in ("DONE", "DELIVERED"),
            "return_reason": getattr(o, "reason", "") or "-",
        }
        for o in allowed_orders
    ]

    return render(
        request,
        "returnshop/returnshop_create.html",
        {
            "scan_value": scan_value,
            "rows_error": rows_error,
            "rows_allowed": rows_allowed,
            "notfound": notfound,
            "error_count": len(rows_error),
            "found_count": len(rows_allowed),
            "total_count": len(scanned_codes),
        },
    )


# =========================
# DETAIL (Batch detail)
# =========================
@login_required
def returnshop_detail(request, pk):
    batch = get_object_or_404(
        ReturnShopBatch.objects.prefetch_related(
            Prefetch(
                "labels",
                queryset=ReturnShopLabel.objects.only("id", "batch_id", "code", "mode", "shop_name").order_by("id"),
            )
        ),
        pk=pk,
    )
    cancelled = _batch_status_cancelled()

    edit_mode = request.GET.get("edit") == "1"
    edit_key = _scan_key_edit_text(pk)
    edit_scan_value = request.session.get(edit_key, "")

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "scan_add":
            edit_scan_value = request.POST.get("scan_codes", "") or ""
            request.session[edit_key] = edit_scan_value
            request.session.modified = True

        if action == "complete":
            if batch.status != _batch_status_done():
                with transaction.atomic():
                    batch.status = _batch_status_done()
                    batch.save(update_fields=["status"])
                    for it in batch.items.select_related("order").all():
                        _set_cod(it.order, 0)
                        _set_order_status(it.order, _status_returned())
            messages.success(request, "Batch completed.")
            return redirect("returnshop_detail", pk=batch.id)

        if action == "undo_complete":
            if batch.status == _batch_status_done():
                with transaction.atomic():
                    batch.status = _batch_status_pending()
                    batch.save(update_fields=["status"])
                    for it in batch.items.select_related("order").all():
                        if it.cod_before is not None:
                            _set_cod(it.order, it.cod_before)
                        _set_order_status(it.order, _status_returning())
            messages.success(request, "Undo complete success.")
            return redirect("returnshop_detail", pk=batch.id)

        if action == "cancel":
            with transaction.atomic():
                batch.status = cancelled
                batch.save(update_fields=["status"])
                for it in batch.items.select_related("order").all():
                    if it.cod_before is not None:
                        _set_cod(it.order, it.cod_before)
                    _set_order_status(it.order, it.status_before or "INBOUND")
            messages.success(request, "Batch cancelled. Orders restored to previous status.")
            return redirect("returnshop_detail", pk=batch.id)

        if action == "undo_cancel":
            if batch.status == cancelled:
                with transaction.atomic():
                    batch.status = _batch_status_pending()
                    batch.save(update_fields=["status"])
                    for it in batch.items.select_related("order").all():
                        if it.cod_before is not None:
                            _set_cod(it.order, it.cod_before)
                        _set_order_status(it.order, _status_returning())
            messages.success(request, "Undo cancel success.")
            return redirect("returnshop_detail", pk=batch.id)

        if action == "remove_item":
            if batch.status in (_batch_status_done(), cancelled):
                messages.error(request, "Cannot edit DONE/CANCELLED batch.")
                return redirect("returnshop_detail", pk=batch.id)

            item_id = request.POST.get("item_id") or ""
            if item_id.isdigit():
                it = (
                    ReturnShopBatchItem.objects.filter(batch=batch, id=int(item_id))
                    .select_related("order")
                    .first()
                )
                if it:
                    with transaction.atomic():
                        if it.cod_before is not None:
                            _set_cod(it.order, it.cod_before)
                        _set_order_status(it.order, it.status_before or "INBOUND")
                        it.delete()
            messages.success(request, "Removed shipment (restored previous status).")
            return redirect(f"{request.path}?edit=1")

    items = batch.items.select_related("order", "order__seller").all().order_by("id")
    rows = []
    for it in items:
        o = it.order
        rows.append(
            {
                "item_id": it.id,
                "tracking_no": getattr(o, "tracking_no", "-"),
                "tracking_url": _order_detail_url(o),
                "seller_name": _seller_name(o),
                "receiver_location": _receiver_location(o),
                "customer_name": _customer_name(o),
                "customer_phone": _customer_phone(o),
                "price": _price(o),
                "cod": _cod(o),
                "order_status": _display_status(_order_status(o)) or "-",
            }
        )

    labels = ReturnShopLabel.objects.filter(batch=batch).order_by("id")

    progress_map = _build_batch_progress_map([batch])
    batch_progress = progress_map.get(
        batch.id,
        {"total_count": 0, "done_count": 0, "pending_count": 0, "label": batch.status},
    )

    return render(
        request,
        "returnshop/returnshop_detail.html",
        {
            "batch": batch,
            "rows": rows,
            "total_orders": len(rows),
            "edit_mode": edit_mode and batch.status == _batch_status_pending(),
            "edit_scan_value": edit_scan_value,
            "labels": labels,
            "cancelled_value": cancelled,
            "batch_progress": batch_progress,
        },
    )


# =========================
# LABELS (Merge / No Merge)
# =========================
@login_required
def returnshop_labels(request, pk):
    batch = get_object_or_404(ReturnShopBatch, pk=pk)

    items = list(batch.items.select_related("order", "order__seller").all().order_by("id"))
    if not items:
        messages.error(request, "No orders in this batch.")
        return redirect("returnshop_detail", pk=batch.id)

    existing_labels = list(ReturnShopLabel.objects.filter(batch=batch).order_by("-id"))

    shop_label_map = {}
    merge_label = None
    for lb in existing_labels:
        if getattr(lb, "mode", "") == getattr(ReturnShopLabel, "MODE_SHOP", "SHOP") and getattr(lb, "shop_name", ""):
            if lb.shop_name not in shop_label_map:
                shop_label_map[lb.shop_name] = lb
        if merge_label is None and getattr(lb, "mode", "") == getattr(ReturnShopLabel, "MODE_MERGE", "MERGE"):
            merge_label = lb

    used_item_ids = set(
        ReturnShopLabelItem.objects.filter(label__batch=batch).values_list("batch_item_id", flat=True)
    )

    remaining_items = [it for it in items if it.id not in used_item_ids]

    by_shop_remaining = defaultdict(list)
    for it in remaining_items:
        by_shop_remaining[_seller_name(it.order)].append(it)

    by_shop_all = defaultdict(list)
    for it in items:
        by_shop_all[_seller_name(it.order)].append(it)

    shop_groups = []
    gid = 0
    for shop, all_items in sorted(by_shop_all.items(), key=lambda x: x[0].lower()):
        gid += 1
        rem_items = by_shop_remaining.get(shop, [])
        shop_groups.append(
            {
                "gid": str(gid),
                "shop": shop,
                "count_all": len(all_items),
                "remaining_count": len(rem_items),
                "orders": [{"tracking": it.order.tracking_no, "tracking_url": _order_detail_url(it.order)} for it in all_items],
                "remaining_item_ids": [it.id for it in rem_items],
                "saved_label": shop_label_map.get(shop),
            }
        )

    shop_groups_no_merge = [g for g in shop_groups if (g["saved_label"] is not None) or (g["remaining_count"] > 0)]

    total_shops = len(shop_groups)
    total_pc = len(items)
    remaining_pc = len(remaining_items)
    shop_summary = [{"shop": g["shop"], "count": g["count_all"]} for g in shop_groups]

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "delete_label":
            label_id = request.POST.get("label_id") or ""
            if label_id.isdigit():
                lb = ReturnShopLabel.objects.filter(id=int(label_id), batch=batch).first()
                if lb:
                    lb.delete()
                    messages.success(request, "Undo save success.")
            return redirect("returnshop_labels", pk=batch.id)

        if action == "undo_merge_all":
            ReturnShopLabel.objects.filter(batch=batch, mode=getattr(ReturnShopLabel, "MODE_MERGE", "MERGE")).delete()
            messages.success(request, "Undo Merge All success.")
            return redirect("returnshop_labels", pk=batch.id)

        if action == "undo_no_merge_all":
            ReturnShopLabel.objects.filter(batch=batch, mode=getattr(ReturnShopLabel, "MODE_SHOP", "SHOP")).delete()
            messages.success(request, "Undo No Merge All success.")
            return redirect("returnshop_labels", pk=batch.id)

        ship_to_address = (request.POST.get("ship_to_address") or "").strip()
        ship_to_phone = (request.POST.get("ship_to_phone") or "").strip()
        cod_amount = _as_decimal(request.POST.get("cod_amount") or "0")

        def _require_dest():
            if not ship_to_address:
                messages.error(request, "Please input Destination Location.")
                return False
            if not ship_to_phone:
                messages.error(request, "Please input Phone Number.")
                return False
            return True

        if action == "create_shop_label":
            if not _require_dest():
                return redirect("returnshop_labels", pk=batch.id)

            gid_value = (request.POST.get("gid") or "").strip()
            gid_map = {g["gid"]: g for g in shop_groups}
            g = gid_map.get(gid_value)
            if not g:
                messages.error(request, "Shop group not found.")
                return redirect("returnshop_labels", pk=batch.id)

            remaining_ids = g["remaining_item_ids"]
            if not remaining_ids:
                messages.error(request, "This shop has no remaining items (maybe already merged).")
                return redirect("returnshop_labels", pk=batch.id)

            remaining_shop_items = [it for it in remaining_items if it.id in set(remaining_ids)]
            if not remaining_shop_items:
                messages.error(request, "No remaining items to save.")
                return redirect("returnshop_labels", pk=batch.id)

            with transaction.atomic():
                lb = ReturnShopLabel.objects.create(
                    batch=batch,
                    ship_to_address=ship_to_address,
                    ship_to_phone=ship_to_phone,
                    cod_amount=cod_amount,
                    mode=getattr(ReturnShopLabel, "MODE_SHOP", "SHOP"),
                    shop_name=g["shop"],
                )
                lb.code = _label_code(batch.id, lb.id)
                lb.save(update_fields=["code"])

                for it in remaining_shop_items:
                    ReturnShopLabelItem.objects.create(label=lb, batch_item=it)

            messages.success(request, f"Saved: {g['shop']} ({len(remaining_shop_items)} PC)")
            return redirect("returnshop_labels", pk=batch.id)

        if action == "create_merge_label":
            if not _require_dest():
                return redirect("returnshop_labels", pk=batch.id)

            selected_gids = request.POST.getlist("selected_gid")
            selected_gids = [str(x).strip() for x in selected_gids if str(x).strip()]
            if not selected_gids:
                messages.error(request, "Please select at least 1 shop group to merge.")
                return redirect("returnshop_labels", pk=batch.id)

            gid_map = {g["gid"]: g for g in shop_groups}
            selected_item_ids = []
            selected_shop_names = []

            for sgid in selected_gids:
                g = gid_map.get(sgid)
                if not g:
                    continue
                selected_shop_names.append(g["shop"])
                selected_item_ids += g["remaining_item_ids"]

            selected_item_ids = list(dict.fromkeys(selected_item_ids))
            if not selected_item_ids:
                messages.error(request, "Selected shops have no remaining items (already saved/merged).")
                return redirect("returnshop_labels", pk=batch.id)

            selected_items = [it for it in remaining_items if it.id in set(selected_item_ids)]
            if not selected_items:
                messages.error(request, "No remaining items to merge.")
                return redirect("returnshop_labels", pk=batch.id)

            with transaction.atomic():
                lb = ReturnShopLabel.objects.create(
                    batch=batch,
                    ship_to_address=ship_to_address,
                    ship_to_phone=ship_to_phone,
                    cod_amount=cod_amount,
                    mode=getattr(ReturnShopLabel, "MODE_MERGE", "MERGE"),
                    shop_name=" + ".join(selected_shop_names),
                )
                lb.code = _label_code(batch.id, lb.id)
                lb.save(update_fields=["code"])

                for it in selected_items:
                    ReturnShopLabelItem.objects.create(label=lb, batch_item=it)

            messages.success(request, f"Merged saved ({len(selected_items)} PC)")
            return redirect("returnshop_labels", pk=batch.id)

    return render(
        request,
        "returnshop/returnshop_labels.html",
        {
            "batch": batch,
            "shop_groups": shop_groups,
            "shop_groups_no_merge": shop_groups_no_merge,
            "existing_labels": existing_labels,
            "total_shops": total_shops,
            "total_pc": total_pc,
            "remaining_pc": remaining_pc,
            "shop_summary": shop_summary,
            "merge_label": merge_label,
        },
    )


# =========================
# LABEL DETAIL
# =========================
@login_required
def returnshop_label_detail(request, pk):
    label = get_object_or_404(ReturnShopLabel, pk=pk)

    li = ReturnShopLabelItem.objects.filter(label=label).select_related(
        "batch_item__order", "batch_item__order__seller"
    )

    seller_set = set()
    rows = []
    for x in li:
        o = x.batch_item.order
        seller_set.add(_seller_name(o))
        rows.append({"tracking": getattr(o, "tracking_no", "-"), "tracking_url": _order_detail_url(o), "seller": _seller_name(o)})

    shop_names = " + ".join(sorted(seller_set)) if seller_set else "-"
    total_pc = len(rows)

    return render(
        request,
        "returnshop/returnshop_label_detail.html",
        {"label": label, "batch": label.batch, "shop_names": shop_names, "total_pc": total_pc, "rows": rows},
    )


# =========================
# PRINT
# =========================
@login_required
def returnshop_label_print(request, pk):
    label = get_object_or_404(ReturnShopLabel, pk=pk)

    li = ReturnShopLabelItem.objects.filter(label=label).select_related(
        "batch_item__order", "batch_item__order__seller"
    )

    seller_set = set()
    for x in li:
        seller_set.add(_seller_name(x.batch_item.order))

    shop_names = " + ".join(sorted(seller_set)) if seller_set else "-"
    total_pc = li.count()

    context = {
        "label": label,
        "batch": label.batch,
        "shop_names": shop_names,
        "total_pc": total_pc,
        "qr_data_uri": _qr_data_uri(label.code),
    }
    return render(request, "returnshop/returnshop_label_print.html", context)