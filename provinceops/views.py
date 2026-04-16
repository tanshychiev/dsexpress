from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from orders.models import Order, OrderActivity
from orders.pricing import apply_pricing
from masterdata.models import Shipper
from provinceops.models import ProvinceBatch, ProvinceBatchItem


# ==========================================================
# Helpers
# ==========================================================
def _scan_key_new_codes() -> str:
    return "province_new_scan_codes"


def _scan_key_new_text() -> str:
    return "province_new_scan_text"


def _scan_key_edit_text(pk: int) -> str:
    return f"province_edit_scan_text_{pk}"


def _parse_codes(raw: str):
    out, seen = [], set()
    for line in (raw or "").splitlines():
        c = (line or "").strip()
        if c and c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _get_field(obj, names, default=""):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v not in ("", None):
                return v
    return default


def _order_status(o: Order) -> str:
    return str(getattr(o, "status", "") or "").upper()


def _can_assign_status(st: str) -> bool:
    return st in ("INBOUND", "CREATED")


def _set_cod(order: Order, value):
    if hasattr(order, "cod"):
        order.cod = value
        order.save(update_fields=["cod"])


def _set_delivery_shipper(order: Order, shipper):
    if hasattr(order, "delivery_shipper"):
        order.delivery_shipper = shipper
        order.save(update_fields=["delivery_shipper"])


def _clear_delivery_shipper(order: Order):
    if hasattr(order, "delivery_shipper"):
        order.delivery_shipper = None
        order.save(update_fields=["delivery_shipper"])


def _set_order_status(order: Order, status: str):
    update_fields = []

    if hasattr(order, "status"):
        order.status = status
        update_fields.append("status")

    if hasattr(order, "done_at"):
        if str(status).upper() in ("DONE", "DELIVERED"):
            order.done_at = timezone.localdate()
        else:
            order.done_at = None
        update_fields.append("done_at")

    if update_fields:
        order.save(update_fields=update_fields)


def _apply_order_pricing(order: Order):
    apply_pricing(order)

    update_fields = []
    for field in ["delivery_fee", "province_fee", "additional_fee", "is_locked"]:
        if hasattr(order, field):
            update_fields.append(field)

    if update_fields:
        order.save(update_fields=update_fields)


def _price(o: Order):
    v = _get_field(o, ["price", "total_price", "amount"], 0)
    try:
        return float(v or 0)
    except Exception:
        return 0


def _cod(o: Order):
    v = getattr(o, "cod", 0)
    try:
        return float(0 if v is None else v)
    except Exception:
        return 0


def _receiver_location(o: Order) -> str:
    v = _get_field(
        o,
        ["receiver_location", "receiver_address", "address", "district", "city", "province"],
        "",
    )
    return v or "-"


def _receiver_address(o: Order) -> str:
    v = _get_field(o, ["receiver_address", "address", "full_address", "note_address"], "")
    return v or "-"


def _customer_name(o: Order) -> str:
    v = _get_field(o, ["customer_name", "receiver_name", "name"], "")
    return v or "-"


def _customer_phone(o: Order) -> str:
    v = _get_field(o, ["customer_phone", "receiver_phone", "phone", "tel"], "")
    return v or "-"


def _seller_name(o: Order) -> str:
    s = getattr(o, "seller", None)
    return getattr(s, "name", "-") or "-"


def _order_detail_url(o: Order) -> str:
    return f"/orders/created/{o.id}/"


def _batch_cancelled_value() -> str:
    return getattr(ProvinceBatch, "STATUS_CANCELLED", "CANCELLED")


def _get_orders_by_tracking(codes):
    qs = (
        Order.objects.filter(tracking_no__in=codes, is_deleted=False)
        .select_related("seller")
    )
    m = {o.tracking_no: o for o in qs}

    found = []
    notfound = []
    for c in codes:
        o = m.get(c)
        if not o:
            notfound.append(c)
            continue
        found.append(o)
    return found, notfound


def _item_supports_cod_before() -> bool:
    return hasattr(ProvinceBatchItem, "cod_before")


def _item_supports_status_before() -> bool:
    return hasattr(ProvinceBatchItem, "status_before")


# ==========================================================
# Activity writer
# ==========================================================
def _create_order_activity(order, user=None, action="", old_status="", new_status="", shipper=None, note=""):
    OrderActivity.objects.create(
        order=order,
        action=action or "",
        old_status=old_status or "",
        new_status=new_status or "",
        actor=user,
        shipper=shipper,
        note=note or "",
    )


def _log_order_change(order, user=None, action="", old_status="", new_status="", shipper=None, note=""):
    _create_order_activity(
        order=order,
        user=user,
        action=action,
        old_status=old_status,
        new_status=new_status,
        shipper=shipper,
        note=note,
    )


# ==========================================================
# LIST
# ==========================================================
@login_required
def province_list(request):
    status = (request.GET.get("status") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    shipper_id = (request.GET.get("shipper_id") or "").strip()
    searched = request.GET.get("search") == "1"

    qs = ProvinceBatch.objects.select_related("shipper", "created_by").order_by("-id")

    if status:
        qs = qs.filter(status=status.upper())

    if shipper_id.isdigit():
        qs = qs.filter(shipper_id=int(shipper_id))

    if date_from:
        qs = qs.filter(assigned_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(assigned_at__date__lte=date_to)

    qs = qs.annotate(total_pc=Count("items", distinct=True))
    qs = qs.annotate(total_shop=Count("items__order__seller", distinct=True))

    rows = qs if searched else ProvinceBatch.objects.none()

    return render(request, "provinceops/province_list.html", {
        "rows": rows,
        "searched": searched,
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
        "shipper_id": shipper_id,
        "shippers": Shipper.objects.filter(is_active=True).order_by("name"),
    })


# ==========================================================
# NEW (SCAN / CREATE)
# ==========================================================
@login_required
def province_new(request):
    codes_key = _scan_key_new_codes()
    text_key = _scan_key_new_text()

    scanned_codes = request.session.get(codes_key, [])
    scan_value = request.session.get(text_key, "")

    if request.method == "POST":
        action = request.POST.get("action") or ""
        posted_scan = request.POST.get("scan_codes", "") or ""

        request.session[text_key] = posted_scan
        request.session.modified = True

        if action == "scan_add":
            codes = _parse_codes(posted_scan)
            if not codes:
                messages.error(request, "Please scan tracking code(s).")
                return redirect("province_new")

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
            return redirect("province_new")

        if action == "scan_clear":
            request.session[codes_key] = []
            request.session[text_key] = ""
            request.session.modified = True
            messages.success(request, "Cleared scanned list.")
            return redirect("province_new")

        if action == "remove_scan":
            code = (request.POST.get("code") or "").strip()
            existing = request.session.get(codes_key, [])
            if code and code in existing:
                existing = [x for x in existing if x != code]
                request.session[codes_key] = existing
                request.session.modified = True
                messages.success(request, f"Removed {code}.")
            return redirect("province_new")

        if action == "confirm_create":
            shipper_id = (request.POST.get("shipper_id") or "").strip()
            remark = (request.POST.get("remark") or "").strip()
            mode = (request.POST.get("mode") or "save").strip()

            checked_ids = request.POST.getlist("checked_ids")
            checked_ids = [int(x) for x in checked_ids if str(x).isdigit()]

            if not shipper_id.isdigit():
                messages.error(request, "Please assign shipper.")
                return redirect("province_new")

            if not checked_ids:
                messages.error(request, "Please tick at least 1 shipment.")
                return redirect("province_new")

            scanned_codes = request.session.get(codes_key, [])
            found_orders, _notfound_codes = _get_orders_by_tracking(scanned_codes)

            allowed_map = {}
            for o in found_orders:
                if _can_assign_status(_order_status(o)):
                    allowed_map[o.id] = o

            selected_orders = [allowed_map.get(i) for i in checked_ids if allowed_map.get(i)]
            if not selected_orders:
                messages.error(request, "Selected shipments are not allowed. Please remove red ones.")
                return redirect("province_new")

            try:
                with transaction.atomic():
                    batch = ProvinceBatch.objects.create(
                        created_by=request.user,
                        shipper_id=int(shipper_id),
                        assigned_at=timezone.now(),
                        remark=remark,
                        status=ProvinceBatch.STATUS_PENDING,
                    )

                    batch_shipper = getattr(batch, "shipper", None)

                    for o in selected_orders:
                        old_status = _order_status(o)

                        item_kwargs = {"batch": batch, "order": o}
                        if _item_supports_cod_before():
                            item_kwargs["cod_before"] = _cod(o)
                        if _item_supports_status_before():
                            item_kwargs["status_before"] = old_status

                        ProvinceBatchItem.objects.create(**item_kwargs)

                        _set_cod(o, 0)
                        _set_order_status(o, "PROCESSING")
                        _set_delivery_shipper(o, batch_shipper)
                        _apply_order_pricing(o)

                        _log_order_change(
                            order=o,
                            user=request.user,
                            action="ASSIGN_PROVINCE",
                            old_status=old_status,
                            new_status="PROCESSING",
                            shipper=batch_shipper,
                            note=f"Assigned to province batch #{batch.id}",
                        )

                    if mode == "complete":
                        batch.status = ProvinceBatch.STATUS_DONE
                        batch.save(update_fields=["status"])
                        for o in selected_orders:
                            old_status = _order_status(o)
                            _set_cod(o, 0)
                            _set_order_status(o, "DONE")
                            _set_delivery_shipper(o, batch_shipper)
                            _apply_order_pricing(o)

                            _log_order_change(
                                order=o,
                                user=request.user,
                                action="COMPLETE_PROVINCE",
                                old_status=old_status,
                                new_status="DONE",
                                shipper=batch_shipper,
                                note=f"Completed from province batch #{batch.id}",
                            )

                request.session[codes_key] = []
                request.session.modified = True
                messages.success(request, "Province batch created.")
                return redirect("province_detail", pk=batch.id)

            except OperationalError:
                messages.error(
                    request,
                    "Database column missing (cod_before/status_before). Please run makemigrations + migrate, then try again."
                )
                return redirect("province_new")

    scanned_codes = request.session.get(codes_key, [])
    scan_value = request.session.get(text_key, "")
    found_orders, notfound_codes = _get_orders_by_tracking(scanned_codes)

    allowed_orders, error_orders = [], []
    for o in found_orders:
        st = _order_status(o)
        if _can_assign_status(st):
            allowed_orders.append(o)
        else:
            error_orders.append(o)

    rows_error = [{
        "code": o.tracking_no,
        "id": o.id,
        "tracking_no": o.tracking_no,
        "tracking_url": _order_detail_url(o),
        "seller_name": _seller_name(o),
        "receiver_location": _receiver_location(o),
        "customer_name": _customer_name(o),
        "customer_phone": _customer_phone(o),
        "price": _price(o),
        "cod": _cod(o),
        "status": _order_status(o) or "-",
    } for o in error_orders]

    rows_allowed = [{
        "id": o.id,
        "tracking_no": o.tracking_no,
        "tracking_url": _order_detail_url(o),
        "seller_name": _seller_name(o),
        "receiver_location": _receiver_location(o),
        "customer_name": _customer_name(o),
        "customer_phone": _customer_phone(o),
        "price": _price(o),
        "cod": _cod(o),
        "status": _order_status(o) or "-",
    } for o in allowed_orders]

    total_count = len(scanned_codes)
    notfound = len(notfound_codes)
    error_count = len(rows_error)
    found_count = len(rows_allowed)

    return render(request, "provinceops/province_create.html", {
        "scan_value": scan_value,
        "rows_error": rows_error,
        "rows_allowed": rows_allowed,
        "notfound": notfound,
        "error_count": error_count,
        "found_count": found_count,
        "total_count": total_count,
        "shippers": Shipper.objects.filter(is_active=True).order_by("name"),
    })


# ==========================================================
# DETAIL (EDIT + CHANGE SHIPPER + COMPLETE + CANCEL)
# ==========================================================
@login_required
def province_detail(request, pk):
    batch = get_object_or_404(ProvinceBatch, pk=pk)
    cancelled_value = _batch_cancelled_value()

    edit_mode = request.GET.get("edit") == "1"
    edit_text_key = _scan_key_edit_text(pk)
    edit_scan_value = request.session.get(edit_text_key, "")

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "change_shipper":
            if batch.status in (ProvinceBatch.STATUS_DONE, cancelled_value):
                messages.error(request, "Cannot change shipper when batch is DONE/CANCELLED.")
                return redirect("province_detail", pk=batch.id)

            shipper_id = (request.POST.get("shipper_id") or "").strip()
            if not shipper_id.isdigit():
                messages.error(request, "Please select shipper.")
                return redirect(f"{request.path}?edit=1")

            new_shipper = Shipper.objects.filter(pk=int(shipper_id)).first()
            old_shipper = getattr(batch, "shipper", None)

            batch.shipper_id = int(shipper_id)
            batch.save(update_fields=["shipper"])

            for it in batch.items.select_related("order").all():
                o = it.order
                _set_delivery_shipper(o, new_shipper)
                _apply_order_pricing(o)

                _log_order_change(
                    order=o,
                    user=request.user,
                    action="CHANGE_PROVINCE_SHIPPER",
                    old_status=_order_status(o),
                    new_status=_order_status(o),
                    shipper=new_shipper,
                    note=f"Province shipper changed from {getattr(old_shipper, 'name', '-') or '-'} to {getattr(new_shipper, 'name', '-') or '-'}",
                )

            messages.success(request, "✅ Shipper updated.")
            return redirect(f"{request.path}?edit=1")

        if action == "complete":
            if batch.status != ProvinceBatch.STATUS_DONE:
                with transaction.atomic():
                    batch.status = ProvinceBatch.STATUS_DONE
                    batch.save(update_fields=["status"])
                    for it in batch.items.select_related("order").all():
                        old_status = _order_status(it.order)
                        _set_cod(it.order, 0)
                        _set_order_status(it.order, "DONE")
                        _set_delivery_shipper(it.order, getattr(batch, "shipper", None))
                        _apply_order_pricing(it.order)

                        _log_order_change(
                            order=it.order,
                            user=request.user,
                            action="COMPLETE_PROVINCE",
                            old_status=old_status,
                            new_status="DONE",
                            shipper=getattr(batch, "shipper", None),
                            note=f"Completed from province batch #{batch.id}",
                        )

            messages.success(request, "✅ Batch completed.")
            return redirect("province_detail", pk=batch.id)

        if action == "undo_complete":
            if batch.status == ProvinceBatch.STATUS_DONE:
                with transaction.atomic():
                    batch.status = ProvinceBatch.STATUS_PENDING
                    batch.save(update_fields=["status"])
                    for it in batch.items.select_related("order").all():
                        old_status = _order_status(it.order)

                        if hasattr(it, "cod_before") and it.cod_before is not None:
                            _set_cod(it.order, it.cod_before)
                        _set_order_status(it.order, "PROCESSING")
                        _set_delivery_shipper(it.order, getattr(batch, "shipper", None))
                        _apply_order_pricing(it.order)

                        _log_order_change(
                            order=it.order,
                            user=request.user,
                            action="UNDO_COMPLETE_PROVINCE",
                            old_status=old_status,
                            new_status="PROCESSING",
                            shipper=getattr(batch, "shipper", None),
                            note=f"Undo complete from province batch #{batch.id}",
                        )

            messages.success(request, "↩️ Undo complete success.")
            return redirect("province_detail", pk=batch.id)

        if action == "cancel":
            if batch.status != cancelled_value:
                with transaction.atomic():
                    batch.status = cancelled_value
                    batch.save(update_fields=["status"])
                    for it in batch.items.select_related("order").all():
                        old_status = _order_status(it.order)

                        if hasattr(it, "cod_before") and it.cod_before is not None:
                            _set_cod(it.order, it.cod_before)
                        restore_status = getattr(it, "status_before", "") or "INBOUND"
                        _set_order_status(it.order, restore_status)
                        _clear_delivery_shipper(it.order)

                        _log_order_change(
                            order=it.order,
                            user=request.user,
                            action="CANCEL_PROVINCE_BATCH",
                            old_status=old_status,
                            new_status=restore_status,
                            shipper=getattr(batch, "shipper", None),
                            note=f"Cancelled province batch #{batch.id}",
                        )

            messages.success(request, "🛑 Batch cancelled.")
            return redirect("province_detail", pk=batch.id)

        if action == "undo_cancel":
            if batch.status == cancelled_value:
                with transaction.atomic():
                    batch.status = ProvinceBatch.STATUS_PENDING
                    batch.save(update_fields=["status"])
                    for it in batch.items.select_related("order").all():
                        old_status = _order_status(it.order)

                        if hasattr(it, "cod_before") and it.cod_before is not None:
                            _set_cod(it.order, it.cod_before)
                        _set_order_status(it.order, "PROCESSING")
                        _set_delivery_shipper(it.order, getattr(batch, "shipper", None))
                        _apply_order_pricing(it.order)

                        _log_order_change(
                            order=it.order,
                            user=request.user,
                            action="UNDO_CANCEL_PROVINCE_BATCH",
                            old_status=old_status,
                            new_status="PROCESSING",
                            shipper=getattr(batch, "shipper", None),
                            note=f"Undo cancel province batch #{batch.id}",
                        )

            messages.success(request, "↩️ Undo cancel success.")
            return redirect("province_detail", pk=batch.id)

        if batch.status in (ProvinceBatch.STATUS_DONE, cancelled_value) and action in ("scan_add", "remove_item"):
            messages.error(request, "Batch is DONE/CANCELLED. Cannot edit.")
            return redirect("province_detail", pk=batch.id)

        if action == "remove_item":
            item_id = (request.POST.get("item_id") or "").strip()
            if not item_id.isdigit():
                return redirect(f"{request.path}?edit=1")

            it = ProvinceBatchItem.objects.filter(batch=batch, id=int(item_id)).select_related("order").first()
            if it:
                with transaction.atomic():
                    o = it.order
                    old_status = _order_status(o)

                    if hasattr(it, "cod_before") and it.cod_before is not None:
                        _set_cod(o, it.cod_before)

                    restore_status = getattr(it, "status_before", "") or "INBOUND"
                    _set_order_status(o, restore_status)
                    _clear_delivery_shipper(o)
                    it.delete()

                    _log_order_change(
                        order=o,
                        user=request.user,
                        action="REMOVE_FROM_PROVINCE_BATCH",
                        old_status=old_status,
                        new_status=restore_status,
                        shipper=getattr(batch, "shipper", None),
                        note=f"Removed from province batch #{batch.id}",
                    )

                messages.success(request, "🗑 Removed shipment (restored previous status).")
            return redirect(f"{request.path}?edit=1")

        if action == "scan_add":
            scan_raw = request.POST.get("scan_codes", "") or ""

            request.session[edit_text_key] = scan_raw
            request.session.modified = True
            edit_scan_value = scan_raw

            codes = _parse_codes(scan_raw)
            if not codes:
                messages.error(request, "Please scan tracking code(s).")
                return redirect(f"{request.path}?edit=1")

            existing_ids = set(batch.items.values_list("order_id", flat=True))
            found_orders, notfound_codes = _get_orders_by_tracking(codes)

            added = 0
            error_list = []

            with transaction.atomic():
                for o in found_orders:
                    st = _order_status(o)

                    if not _can_assign_status(st):
                        error_list.append(f"{o.tracking_no} ({st})")
                        continue

                    if o.id in existing_ids:
                        continue

                    item_kwargs = {
                        "batch": batch,
                        "order": o,
                    }
                    if hasattr(ProvinceBatchItem, "cod_before"):
                        item_kwargs["cod_before"] = _cod(o)
                    if hasattr(ProvinceBatchItem, "status_before"):
                        item_kwargs["status_before"] = st

                    ProvinceBatchItem.objects.create(**item_kwargs)

                    _set_cod(o, 0)
                    _set_order_status(o, "PROCESSING")
                    _set_delivery_shipper(o, getattr(batch, "shipper", None))
                    _apply_order_pricing(o)
                    added += 1

                    _log_order_change(
                        order=o,
                        user=request.user,
                        action="ADD_TO_PROVINCE_BATCH",
                        old_status=st,
                        new_status="PROCESSING",
                        shipper=getattr(batch, "shipper", None),
                        note=f"Added to province batch #{batch.id}",
                    )

            if notfound_codes:
                messages.warning(request, f"{len(notfound_codes)} not found.")
            if error_list:
                messages.error(request, "Not allowed: " + ", ".join(error_list))
            messages.success(request, f"✅ Added {added} shipment(s).")

            return redirect(f"{request.path}?edit=1")

    items = batch.items.select_related("order", "order__seller").all().order_by("id")

    rows = []
    for it in items:
        o = it.order
        rows.append({
            "item_id": it.id,
            "order_id": o.id,
            "tracking_no": getattr(o, "tracking_no", "-"),
            "tracking_url": _order_detail_url(o),
            "seller_name": getattr(getattr(o, "seller", None), "name", "-") or "-",
            "receiver_location": _receiver_location(o),
            "receiver_address": _receiver_address(o),
            "customer_name": _customer_name(o),
            "customer_phone": _customer_phone(o),
            "price": _price(o),
            "cod": _cod(o),
            "order_status": _order_status(o) or "-",
        })

    return render(request, "provinceops/province_detail.html", {
        "batch": batch,
        "rows": rows,
        "total_orders": len(rows),
        "edit_mode": edit_mode and batch.status not in (ProvinceBatch.STATUS_DONE, cancelled_value),
        "cancelled_value": cancelled_value,
        "edit_scan_value": edit_scan_value,
        "shippers": Shipper.objects.filter(is_active=True).order_by("name"),
    })


# ==========================================================
# PRINT
# ==========================================================
@login_required
def province_print(request, pk):
    batch = get_object_or_404(ProvinceBatch, pk=pk)
    items = batch.items.select_related("order", "order__seller").all().order_by("id")
    html = render_to_string("provinceops/province_print.html", {
        "batch": batch,
        "items": items,
    })
    return HttpResponse(html)