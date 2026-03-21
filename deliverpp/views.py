from __future__ import annotations

import re
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, IntegerField, Q, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from masterdata.models import Shipper
from orders.activity import add_order_activity
from orders.audit import add_audit_log
from orders.models import AuditLog, Order, OrderActivity
from .models import PPDeliveryBatch, PPDeliveryItem


RET_RE = re.compile(r"^(RTS|RET)-(\d+)(?:-(\d+))?$", re.I)
ALLOWED_ORDER_STATUS = {"CREATED", "INBOUND"}


# ============================================================
# Helpers
# ============================================================
def _field_names(model_cls) -> Set[str]:
    try:
        return {f.name for f in model_cls._meta.get_fields()}
    except Exception:
        return set()


def _has_field(model_cls, name: str) -> bool:
    return name in _field_names(model_cls)


def get_return_models():
    try:
        ReturnBatch = apps.get_model("returnshop", "ReturnShopBatch")
        ReturnBatchItem = apps.get_model("returnshop", "ReturnShopBatchItem")
        ReturnLabel = apps.get_model("returnshop", "ReturnShopLabel")
        ReturnLabelItem = apps.get_model("returnshop", "ReturnShopLabelItem")
        return ReturnBatch, ReturnBatchItem, ReturnLabel, ReturnLabelItem
    except Exception:
        return None, None, None, None


def _parse_lines(raw: str) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for line in raw.splitlines():
        code = (line or "").strip()
        if code:
            out.append(code)
    return out


def _normalize_ret_code(code: str) -> Optional[str]:
    c = (code or "").strip().upper()
    m = RET_RE.match(c)
    if not m:
        return None
    prefix = m.group(1).upper()
    master_id = m.group(2)
    label_id = m.group(3)
    return f"{prefix}-{master_id}-{label_id}" if label_id else f"{prefix}-{master_id}"


def _ret_parts(code: str) -> Optional[Tuple[str, int, Optional[int]]]:
    norm = _normalize_ret_code(code)
    if not norm:
        return None
    m = RET_RE.match(norm)
    if not m:
        return None
    prefix = m.group(1).upper()
    master_id = int(m.group(2))
    label_id = int(m.group(3)) if m.group(3) else None
    return prefix, master_id, label_id


def _get_return_label_cod(lb) -> Decimal:
    if not lb:
        return Decimal("0.00")

    for name in ("cod_amount", "cod", "amount_cod", "cod_usd"):
        v = getattr(lb, name, None)
        if v is not None:
            try:
                return Decimal(str(v or "0")).quantize(Decimal("0.00"))
            except Exception:
                pass
    return Decimal("0.00")


def _detail_url(batch_id: int, edit: bool = False) -> str:
    base = reverse("deliverpp_detail", kwargs={"batch_id": batch_id})
    return f"{base}?edit=1" if edit else base


def _get_pp_shippers():
    qs = Shipper.objects.all()
    try:
        return qs.filter(shipper_type="DELIVERY")
    except Exception:
        return qs


def _order_is_allowed_for_pp(o: Order) -> bool:
    return (getattr(o, "status", "") or "").upper() in ALLOWED_ORDER_STATUS


def _order_is_in_any_return_batch(o: Order) -> bool:
    try:
        return o.returnshop_batch_items.exists()
    except Exception:
        return False


def _order_is_in_any_pp(o: Order, exclude_batch_id: Optional[int] = None) -> bool:
    qs = PPDeliveryItem.objects.filter(order_id=o.id).exclude(
        batch__status__in=[
            getattr(PPDeliveryBatch, "STATUS_CANCELLED", "CANCELLED"),
            getattr(PPDeliveryBatch, "STATUS_DONE", "DONE"),
        ]
    )
    if exclude_batch_id:
        qs = qs.exclude(batch_id=exclude_batch_id)
    return qs.exists()


def _batch_get_label_codes(batch: PPDeliveryBatch) -> List[str]:
    val = (
        getattr(batch, "return_label_codes", None)
        or getattr(batch, "return_codes", None)
        or getattr(batch, "return_labels", None)
        or []
    )
    return list(val or [])


def _batch_set_label_codes(batch: PPDeliveryBatch, codes: List[str]):
    if hasattr(batch, "return_label_codes"):
        batch.return_label_codes = codes
    elif hasattr(batch, "return_codes"):
        batch.return_codes = codes
    elif hasattr(batch, "return_labels"):
        batch.return_labels = codes


def _batch_get_master_ids(batch: PPDeliveryBatch) -> List[int]:
    val = (
        getattr(batch, "return_batch_ids", None)
        or getattr(batch, "return_batches", None)
        or getattr(batch, "return_batch_codes", None)
        or []
    )
    out: List[int] = []
    for x in (val or []):
        try:
            out.append(int(x))
        except Exception:
            continue
    return out


def _batch_set_master_ids(batch: PPDeliveryBatch, ids: List[int]):
    if hasattr(batch, "return_batch_ids"):
        batch.return_batch_ids = ids
    elif hasattr(batch, "return_batches"):
        batch.return_batches = ids
    elif hasattr(batch, "return_batch_codes"):
        batch.return_batch_codes = ids


def _pp_used_return_label_code(label_code: str, exclude_batch_id: Optional[int] = None) -> bool:
    qs = PPDeliveryBatch.objects.exclude(
        status=getattr(PPDeliveryBatch, "STATUS_CANCELLED", "CANCELLED")
    )
    if exclude_batch_id:
        qs = qs.exclude(id=exclude_batch_id)

    try:
        return qs.filter(return_label_codes__contains=[label_code]).exists()
    except Exception:
        for b in qs:
            if label_code in (_batch_get_label_codes(b) or []):
                return True
        return False


def _add_status_activity_and_audit(
    order: Order,
    user,
    action: str,
    old_status: str,
    new_status: str,
    note: str,
    shipper: Optional[Shipper] = None,
):
    add_order_activity(
        order=order,
        action=action,
        user=user,
        shipper=shipper,
        old_status=old_status,
        new_status=new_status,
        note=note,
    )

    add_audit_log(
        module=AuditLog.MODULE_ORDER,
        obj=order,
        action=AuditLog.ACTION_CHANGE_STATUS,
        user=user,
        field_name="status",
        old_value=old_status,
        new_value=new_status,
        note=note,
    )


def _set_order_status_after_pp_assign(
    orders: List[Order],
    is_return: bool,
    user,
    shipper: Optional[Shipper] = None,
):
    if not orders:
        return

    now = timezone.now()

    for order in orders:
        old_status = order.status
        old_shipper = order.delivery_shipper

        if is_return:
            if shipper:
                order.delivery_shipper = shipper

            order.status = Order.STATUS_RETURNING
            order.updated_at = now
            order.updated_by = user

            update_fields = ["status", "updated_at", "updated_by"]
            if shipper:
                update_fields.append("delivery_shipper")

            order.save(update_fields=update_fields)

            if shipper and old_shipper != shipper:
                add_audit_log(
                    module=AuditLog.MODULE_ORDER,
                    obj=order,
                    action=AuditLog.ACTION_ASSIGN_SHIPPER,
                    user=user,
                    field_name="delivery_shipper",
                    old_value=str(old_shipper.name if old_shipper else ""),
                    new_value=str(shipper.name),
                    note="Assigned return shipper from PP batch",
                )

            _add_status_activity_and_audit(
                order=order,
                user=user,
                action=OrderActivity.ACTION_EDIT,
                old_status=old_status,
                new_status=order.status,
                shipper=order.delivery_shipper,
                note="Assigned to PP batch for return delivery",
            )

        else:
            if shipper:
                order.delivery_shipper = shipper
                order.status = Order.STATUS_OUT_FOR_DELIVERY
                order.updated_at = now
                order.updated_by = user
                order.save(update_fields=["delivery_shipper", "status", "updated_at", "updated_by"])

                add_order_activity(
                    order=order,
                    action=OrderActivity.ACTION_ASSIGN,
                    user=user,
                    shipper=shipper,
                    old_status=old_status,
                    new_status=order.status,
                    note=f"Assigned to shipper {shipper.name}",
                )

                add_audit_log(
                    module=AuditLog.MODULE_ORDER,
                    obj=order,
                    action=AuditLog.ACTION_ASSIGN_SHIPPER,
                    user=user,
                    field_name="delivery_shipper",
                    old_value=str(old_shipper.name if old_shipper else ""),
                    new_value=str(shipper.name),
                    note="Assigned shipper from PP batch",
                )

                add_audit_log(
                    module=AuditLog.MODULE_ORDER,
                    obj=order,
                    action=AuditLog.ACTION_CHANGE_STATUS,
                    user=user,
                    field_name="status",
                    old_value=old_status,
                    new_value=order.status,
                    note="Moved to out for delivery from PP batch",
                )

                add_order_activity(
                    order=order,
                    action=OrderActivity.ACTION_OUT_FOR_DELIVERY,
                    user=user,
                    shipper=shipper,
                    old_status=old_status,
                    new_status=order.status,
                    note="Moved to out for delivery",
                )
            else:
                order.status = Order.STATUS_OUT_FOR_DELIVERY
                order.updated_at = now
                order.updated_by = user
                order.save(update_fields=["status", "updated_at", "updated_by"])

                _add_status_activity_and_audit(
                    order=order,
                    user=user,
                    action=OrderActivity.ACTION_OUT_FOR_DELIVERY,
                    old_status=old_status,
                    new_status=order.status,
                    shipper=order.delivery_shipper,
                    note="Moved to out for delivery from PP batch",
                )


def _reset_order_status_if_removed(order_ids: List[int], user, to_status: str = "INBOUND"):
    if not order_ids:
        return

    still_in_pp_ids = set(
        PPDeliveryItem.objects.filter(order_id__in=order_ids).values_list("order_id", flat=True)
    )

    safe_ids: List[int] = []
    for oid in order_ids:
        if oid in still_in_pp_ids:
            continue
        o = Order.objects.filter(id=oid).first()
        if not o:
            continue
        if _order_is_in_any_return_batch(o):
            continue
        safe_ids.append(oid)

    if not safe_ids:
        return

    rows = list(Order.objects.filter(id__in=safe_ids))
    now = timezone.now()

    for order in rows:
        old_status = order.status
        old_shipper = order.delivery_shipper

        order.status = to_status
        order.delivery_shipper = None
        order.updated_at = now
        order.updated_by = user
        order.save(update_fields=["status", "delivery_shipper", "updated_at", "updated_by"])

        _add_status_activity_and_audit(
            order=order,
            user=user,
            action=OrderActivity.ACTION_INBOUND,
            old_status=old_status,
            new_status=order.status,
            shipper=old_shipper,
            note="Removed from PP batch and reset to inbound",
        )

        if old_shipper:
            add_audit_log(
                module=AuditLog.MODULE_ORDER,
                obj=order,
                action=AuditLog.ACTION_ASSIGN_SHIPPER,
                user=user,
                field_name="delivery_shipper",
                old_value=str(old_shipper.name),
                new_value="",
                note="Delivery shipper cleared after removing from PP batch",
            )


def _reset_return_order_status_if_removed(order_ids: List[int], user):
    if not order_ids:
        return

    still_in_pp_ids = set(
        PPDeliveryItem.objects.filter(order_id__in=order_ids).values_list("order_id", flat=True)
    )

    safe_ids: List[int] = []
    for oid in order_ids:
        if oid in still_in_pp_ids:
            continue
        o = Order.objects.filter(id=oid).first()
        if not o:
            continue
        safe_ids.append(oid)

    if not safe_ids:
        return

    rows = list(Order.objects.filter(id__in=safe_ids))
    now = timezone.now()

    for order in rows:
        old_status = order.status
        old_shipper = order.delivery_shipper

        order.status = Order.STATUS_RETURN_ASSIGNED
        order.updated_at = now
        order.updated_by = user
        order.save(update_fields=["status", "updated_at", "updated_by"])

        _add_status_activity_and_audit(
            order=order,
            user=user,
            action=OrderActivity.ACTION_RETURN_ASSIGNED,
            old_status=old_status,
            new_status=order.status,
            shipper=old_shipper,
            note="Removed from PP batch and reset to return assigned",
        )


def _safe_recalc_batch_totals(batch: PPDeliveryBatch, save: bool = True):
    total_pc = PPDeliveryItem.objects.filter(batch=batch).count()
    shipment_cnt = PPDeliveryItem.objects.filter(
        batch=batch,
        source_type=PPDeliveryItem.SOURCE_NORMAL,
    ).count()
    return_batch_cnt = len(set(_batch_get_master_ids(batch)))
    total_count = int(shipment_cnt) + int(return_batch_cnt)

    update_fields = []
    if hasattr(batch, "total_pc"):
        batch.total_pc = int(total_pc)
        update_fields.append("total_pc")
    if hasattr(batch, "shipment_count"):
        batch.shipment_count = int(shipment_cnt)
        update_fields.append("shipment_count")
    if hasattr(batch, "return_batch_count"):
        batch.return_batch_count = int(return_batch_cnt)
        update_fields.append("return_batch_count")
    if hasattr(batch, "total_count"):
        batch.total_count = int(total_count)
        update_fields.append("total_count")

    if save and update_fields:
        batch.save(update_fields=update_fields)


# ============================================================
# Return helpers
# ============================================================
def _return_master_exists(ReturnBatch, master_id: int) -> Tuple[bool, str, Optional[object]]:
    if not ReturnBatch:
        return False, "Return models not ready", None

    b = ReturnBatch.objects.filter(id=master_id).first()
    if not b:
        return False, "Return batch not found", None

    st = (getattr(b, "status", "") or "").upper()
    if st != "PENDING":
        return False, f"Return status = {st}", b

    return True, "OK", b


def _return_label_exists(ReturnLabel, master_id: int, label_id: int) -> Tuple[bool, str, Optional[object]]:
    if not ReturnLabel:
        return False, "Return label model not ready", None

    lb = ReturnLabel.objects.filter(id=label_id, batch_id=master_id).first()
    if not lb:
        return False, "Return label not found", None

    return True, "OK", lb


def _expand_master_to_label_codes(ReturnLabel, prefix: str, master_id: int) -> List[str]:
    if not ReturnLabel:
        return []
    label_ids = list(ReturnLabel.objects.filter(batch_id=master_id).values_list("id", flat=True))
    return [f"{prefix}-{master_id}-{lid}" for lid in label_ids]


def _get_orders_from_return_label_code(label_code: str, ReturnLabelItem) -> Tuple[int, int, List[Order], str]:
    parts = _ret_parts(label_code)
    if not parts:
        return 0, 0, [], ""

    prefix, master_id, label_id = parts
    if not label_id:
        return master_id, 0, [], ""

    qs = (
        ReturnLabelItem.objects
        .select_related("batch_item", "batch_item__order")
        .filter(label_id=label_id, label__batch_id=master_id)
    )

    orders: List[Order] = []
    for li in qs:
        bi = getattr(li, "batch_item", None)
        o = getattr(bi, "order", None) if bi else None
        if o:
            orders.append(o)

    return master_id, label_id, orders, f"{prefix}-{master_id}-{label_id}"


def _collect_return_orders_for_pp(
    ret_codes: List[str],
    ReturnBatch,
    ReturnLabel,
    ReturnLabelItem,
    exclude_batch_id: Optional[int] = None,
) -> Tuple[List[Tuple[Order, str]], List[int], List[str], Dict[str, str]]:
    ret_orders: List[Tuple[Order, str]] = []
    master_ids_used: Set[int] = set()
    label_codes_used: Set[str] = set()
    code_status: Dict[str, str] = {}

    if not ret_codes:
        return ret_orders, [], [], code_status

    expanded_label_codes: List[str] = []
    for raw in ret_codes:
        norm = _normalize_ret_code(raw)
        if not norm:
            continue

        parts = _ret_parts(norm)
        if not parts:
            continue

        prefix, master_id, label_id = parts
        ok_master, why_master, _rb = _return_master_exists(ReturnBatch, master_id)
        if not ok_master:
            code_status[norm] = why_master
            continue

        if label_id:
            expanded_label_codes.append(f"{prefix}-{master_id}-{label_id}")
        else:
            label_codes = _expand_master_to_label_codes(ReturnLabel, prefix, master_id)
            if not label_codes:
                code_status[norm] = "No labels in this master"
                continue
            expanded_label_codes.extend(label_codes)

    expanded_label_codes = sorted(set(expanded_label_codes))

    for lc in expanded_label_codes:
        parts = _ret_parts(lc)
        if not parts:
            continue

        prefix, master_id, label_id = parts
        if not label_id:
            continue

        ok_label, why_label, _lb = _return_label_exists(ReturnLabel, master_id, label_id)
        if not ok_label:
            code_status[lc] = why_label
            continue

        if _pp_used_return_label_code(lc, exclude_batch_id=exclude_batch_id):
            code_status[lc] = "Return label already assigned"
            continue

        mid, lid, orders, src_code = _get_orders_from_return_label_code(lc, ReturnLabelItem)
        if not orders:
            code_status[lc] = "No orders in this label"
            continue

        allowed_orders: List[Order] = []
        for o in orders:
            if _order_is_in_any_pp(o, exclude_batch_id=exclude_batch_id):
                continue
            allowed_orders.append(o)

        if not allowed_orders:
            code_status[lc] = "All orders already in PP batch"
            continue

        code_status[lc] = "OK"
        label_codes_used.add(src_code)
        master_ids_used.add(mid)
        for o in allowed_orders:
            ret_orders.append((o, src_code))

    return ret_orders, sorted(master_ids_used), sorted(label_codes_used), code_status


# ============================================================
# LIST
# ============================================================
@login_required
def deliverpp_list(request):
    shippers = _get_pp_shippers()

    show_results = (request.GET.get("show") or "") == "1"
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    status = (request.GET.get("status") or "").strip()
    shipper_id = (request.GET.get("shipper_id") or "").strip()

    batches = PPDeliveryBatch.objects.none()

    if show_results:
        qs = PPDeliveryBatch.objects.all()
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if status:
            qs = qs.filter(status=status)
        if shipper_id.isdigit():
            qs = qs.filter(shipper_id=int(shipper_id))

        batches = (
            qs.select_related("created_by", "shipper")
            .annotate(
                total_pc_cnt=Count("items", distinct=True),
                shipment_cnt=Count(
                    "items",
                    filter=Q(items__source_type=PPDeliveryItem.SOURCE_NORMAL),
                    distinct=True,
                ),
                return_batch_cnt=Coalesce(
                    F("return_batch_count"),
                    Value(0),
                    output_field=IntegerField(),
                ),
            )
            .order_by("-id")
        )

    return render(request, "deliverpp/list.html", {
        "show_results": show_results,
        "batches": batches,
        "shippers": shippers,
        "status_choices": PPDeliveryBatch.STATUS_CHOICES,
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
        "shipper_id": shipper_id,
    })


# ============================================================
# CREATE
# ============================================================
@login_required
def pp_delivery_create(request):
    SK_NORMAL = "pp_scan_normal"
    SK_RET = "pp_scan_ret"
    SK_LAST_TEXT = "pp_last_scan_text"

    request.session.setdefault(SK_NORMAL, [])
    request.session.setdefault(SK_RET, [])
    request.session.setdefault(SK_LAST_TEXT, "")

    scan_value = request.session.get(SK_LAST_TEXT, "")

    ReturnBatch, ReturnBatchItem, ReturnLabel, ReturnLabelItem = get_return_models()
    return_ok = all([ReturnBatch, ReturnBatchItem, ReturnLabel, ReturnLabelItem])

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "scan_clear":
            request.session[SK_NORMAL] = []
            request.session[SK_RET] = []
            request.session[SK_LAST_TEXT] = ""
            request.session.modified = True
            return redirect("deliverpp_new")

        if action == "scan_add":
            scan_value = (request.POST.get("scan_codes") or "").strip()
            codes = _parse_lines(scan_value)

            normal_set = set(request.session.get(SK_NORMAL, []))
            ret_set = set(request.session.get(SK_RET, []))

            for c in codes:
                r = _normalize_ret_code(c)
                if r:
                    ret_set.add(r)
                else:
                    normal_set.add(c.strip())

            request.session[SK_NORMAL] = sorted(normal_set)
            request.session[SK_RET] = sorted(ret_set)
            request.session[SK_LAST_TEXT] = scan_value
            request.session.modified = True
            return redirect("deliverpp_new")

        if action == "remove_scan":
            code = (request.POST.get("code") or "").strip()
            r = _normalize_ret_code(code)

            normal_list = request.session.get(SK_NORMAL, [])
            ret_list = request.session.get(SK_RET, [])

            if r:
                ret_list = [x for x in ret_list if x != r]
            else:
                normal_list = [x for x in normal_list if x != code]

            request.session[SK_NORMAL] = normal_list
            request.session[SK_RET] = ret_list
            request.session.modified = True
            return redirect("deliverpp_new")

        if action == "confirm_create":
            shipper_id = (request.POST.get("shipper_id") or "").strip()
            remark = (request.POST.get("remark") or "").strip()

            if not shipper_id.isdigit():
                messages.error(request, "Please select shipper.")
                return redirect("deliverpp_new")

            normal_codes = request.session.get(SK_NORMAL, [])
            ret_codes = request.session.get(SK_RET, [])

            normal_found = list(Order.objects.filter(tracking_no__in=normal_codes))
            normal_ok: List[Order] = []
            for o in normal_found:
                if not _order_is_allowed_for_pp(o):
                    continue
                if _order_is_in_any_return_batch(o):
                    continue
                if _order_is_in_any_pp(o):
                    continue
                normal_ok.append(o)

            ret_orders: List[Tuple[Order, str]] = []
            ret_master_ids: List[int] = []
            ret_label_codes: List[str] = []

            if ret_codes:
                if not return_ok:
                    messages.error(request, "Return models not ready. Check returnshop app.")
                    return redirect("deliverpp_new")

                ret_orders, ret_master_ids, ret_label_codes, _ = _collect_return_orders_for_pp(
                    ret_codes=ret_codes,
                    ReturnBatch=ReturnBatch,
                    ReturnLabel=ReturnLabel,
                    ReturnLabelItem=ReturnLabelItem,
                    exclude_batch_id=None,
                )

            uniq: Dict[int, Tuple[Order, str]] = {}
            for o in normal_ok:
                uniq[o.id] = (o, "")
            for o, sc in ret_orders:
                uniq[o.id] = (o, sc)

            all_pairs = list(uniq.values())
            if not all_pairs:
                messages.error(request, "No allowed orders to create batch.")
                return redirect("deliverpp_new")

            shipment_count = len(normal_ok)
            return_batch_count = len(set(ret_master_ids))
            total_count = int(shipment_count) + int(return_batch_count)
            total_pc = len(all_pairs)

            with transaction.atomic():
                create_kwargs = {
                    "created_by": request.user,
                    "remark": remark,
                    "shipper_id": int(shipper_id),
                    "status": getattr(PPDeliveryBatch, "STATUS_PENDING", "PENDING"),
                }

                if _has_field(PPDeliveryBatch, "assigned_at"):
                    create_kwargs["assigned_at"] = timezone.now()
                if _has_field(PPDeliveryBatch, "total_count"):
                    create_kwargs["total_count"] = int(total_count or 0)
                if _has_field(PPDeliveryBatch, "shipment_count"):
                    create_kwargs["shipment_count"] = int(shipment_count or 0)
                if _has_field(PPDeliveryBatch, "return_batch_count"):
                    create_kwargs["return_batch_count"] = int(return_batch_count or 0)
                if _has_field(PPDeliveryBatch, "total_pc"):
                    create_kwargs["total_pc"] = int(total_pc or 0)

                batch = PPDeliveryBatch.objects.create(**create_kwargs)
                _batch_set_master_ids(batch, sorted(set(ret_master_ids)))
                _batch_set_label_codes(batch, sorted(set(ret_label_codes)))
                batch.save()

                normal_to_update: List[Order] = []
                return_to_update: List[Order] = []

                for o, sc in all_pairs:
                    is_return = bool(sc)
                    PPDeliveryItem.objects.get_or_create(
                        batch=batch,
                        order=o,
                        defaults={
                            "source_type": PPDeliveryItem.SOURCE_RETURN if is_return else PPDeliveryItem.SOURCE_NORMAL,
                            "source_code": sc,
                        },
                    )
                    if is_return:
                        return_to_update.append(o)
                    else:
                        normal_to_update.append(o)

                selected_shipper = Shipper.objects.filter(id=int(shipper_id)).first()
                _set_order_status_after_pp_assign(
                    normal_to_update,
                    is_return=False,
                    shipper=selected_shipper,
                    user=request.user,
                )
                _set_order_status_after_pp_assign(
                    return_to_update,
                    is_return=True,
                    shipper=selected_shipper,
                    user=request.user,
                )
                _safe_recalc_batch_totals(batch, save=True)

            request.session[SK_NORMAL] = []
            request.session[SK_RET] = []
            request.session[SK_LAST_TEXT] = ""
            request.session.modified = True

            messages.success(request, f"Created {batch.code}")
            return redirect("deliverpp_detail", batch_id=batch.id)

    normal_codes = request.session.get(SK_NORMAL, [])
    ret_codes = request.session.get(SK_RET, [])

    found_qs = Order.objects.filter(tracking_no__in=normal_codes).select_related("seller")
    found_map = {o.tracking_no: o for o in found_qs}

    rows_normal_found: List[dict] = []
    rows_normal_notfound: List[str] = []

    for code in normal_codes:
        o = found_map.get(code)
        if not o:
            rows_normal_notfound.append(code)
            continue

        ok = True
        why = ""
        if not _order_is_allowed_for_pp(o):
            ok = False
            why = f"Status {o.status} not allowed"
        elif _order_is_in_any_return_batch(o):
            ok = False
            why = "Already in Return batch"
        elif _order_is_in_any_pp(o):
            ok = False
            why = "Already in PP batch"

        rows_normal_found.append({
            "order": o,
            "ok": ok,
            "why": why,
        })

    rows_return_found: List[dict] = []
    rows_return_notfound: List[str] = []

    if ret_codes:
        if return_ok:
            _ret_orders, _ret_master_ids, ret_label_codes, code_status = _collect_return_orders_for_pp(
                ret_codes=ret_codes,
                ReturnBatch=ReturnBatch,
                ReturnLabel=ReturnLabel,
                ReturnLabelItem=ReturnLabelItem,
                exclude_batch_id=None,
            )

            preview_codes = sorted(set(ret_label_codes or ret_codes))

            for code in preview_codes:
                parts = _ret_parts(code)
                if not parts:
                    rows_return_notfound.append(code)
                    continue

                prefix, master_id, label_id = parts

                rb = ReturnBatch.objects.filter(id=master_id).first() if ReturnBatch else None
                lb = ReturnLabel.objects.filter(id=label_id, batch_id=master_id).first() if (ReturnLabel and label_id) else None

                if not rb:
                    rows_return_notfound.append(code)
                    continue

                pc = 0
                if ReturnLabelItem and lb:
                    try:
                        pc = ReturnLabelItem.objects.filter(label_id=lb.id).count()
                    except Exception:
                        pc = 0

                cod_val = _get_return_label_cod(lb) if lb else Decimal("0.00")
                status_text = code_status.get(code, "OK")

                rows_return_found.append({
                    "code": code,
                    "created_at": getattr(rb, "created_at", None),
                    "shop": getattr(lb, "shop_name", "") if lb else "",
                    "address": getattr(lb, "ship_to_address", "") if lb else "",
                    "phone": getattr(lb, "ship_to_phone", "") if lb else "",
                    "total_pc": pc,
                    "cod": cod_val,
                    "ok": status_text == "OK",
                    "why": "" if status_text == "OK" else status_text,
                })
        else:
            rows_return_notfound = ret_codes[:]

    shipment_delivery_count = len(normal_codes)
    master_ids = []
    for rc in ret_codes:
        p = _ret_parts(rc)
        if p:
            master_ids.append(p[1])

    return_batch_count = len(set(master_ids))
    total_count = shipment_delivery_count + return_batch_count

    return render(request, "deliverpp/create.html", {
        "scan_value": scan_value,
        "total_count": total_count,
        "shipment_delivery_count": shipment_delivery_count,
        "return_batch_count": return_batch_count,
        "rows_normal_found": rows_normal_found,
        "rows_normal_notfound": rows_normal_notfound,
        "rows_return_found": rows_return_found,
        "rows_return_notfound": rows_return_notfound,
        "shippers": _get_pp_shippers(),
    })


# ============================================================
# DETAIL + EDIT
# ============================================================
@login_required
def pp_delivery_detail(request, batch_id: int):
    batch = get_object_or_404(PPDeliveryBatch, id=batch_id)

    ReturnBatch, ReturnBatchItem, ReturnLabel, ReturnLabelItem = get_return_models()
    if not all([ReturnBatch, ReturnBatchItem, ReturnLabel, ReturnLabelItem]):
        ReturnBatch = ReturnBatchItem = ReturnLabel = ReturnLabelItem = None

    SK_ADD_TEXT = f"pp_detail_add_text_{batch.id}"
    SK_REMOVE_IDS = f"pp_detail_remove_ids_{batch.id}"
    SK_REMOVE_RET_LABELS = f"pp_detail_remove_ret_labels_{batch.id}"

    request.session.setdefault(SK_ADD_TEXT, "")
    request.session.setdefault(SK_REMOVE_IDS, [])
    request.session.setdefault(SK_REMOVE_RET_LABELS, [])
    request.session.modified = True

    edit_mode = request.GET.get("edit") == "1"

    def _clear_edit_session():
        request.session[SK_ADD_TEXT] = ""
        request.session[SK_REMOVE_IDS] = []
        request.session[SK_REMOVE_RET_LABELS] = []
        request.session.modified = True

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "enter_edit":
            return redirect(_detail_url(batch.id, edit=True))

        if action == "cancel_edit":
            _clear_edit_session()
            return redirect(_detail_url(batch.id, edit=False))

        if action == "save_reasons":
            updates = []
            for key, val in request.POST.items():
                if key.startswith("reason_"):
                    it_id = key.replace("reason_", "").strip()
                    if it_id.isdigit():
                        updates.append((int(it_id), (val or "").strip()))

            if updates:
                item_map = {
                    it.id: it
                    for it in PPDeliveryItem.objects.filter(batch=batch, id__in=[x[0] for x in updates])
                }
                for it_id, reason in updates:
                    it = item_map.get(it_id)
                    if it:
                        it.reason = reason
                        it.save(update_fields=["reason"])
                messages.success(request, "Reasons saved.")
            else:
                messages.info(request, "No reason changes.")
            return redirect(_detail_url(batch.id, edit=False))

        if action in {
            "stage_add",
            "stage_remove",
            "undo_remove",
            "stage_remove_return",
            "undo_remove_return",
            "confirm_save",
        } and not edit_mode:
            messages.error(request, "Please click Edit first.")
            return redirect(_detail_url(batch.id, edit=False))

        if action == "stage_add":
            request.session[SK_ADD_TEXT] = (request.POST.get("scan_codes") or "").strip()
            request.session.modified = True
            return redirect(_detail_url(batch.id, edit=True))

        if action == "stage_remove":
            item_id = (request.POST.get("item_id") or "").strip()
            if item_id.isdigit():
                s = set(request.session.get(SK_REMOVE_IDS, []))
                s.add(int(item_id))
                request.session[SK_REMOVE_IDS] = sorted(s)
                request.session.modified = True
            return redirect(_detail_url(batch.id, edit=True))

        if action == "undo_remove":
            item_id = (request.POST.get("item_id") or "").strip()
            if item_id.isdigit():
                request.session[SK_REMOVE_IDS] = [
                    x for x in request.session.get(SK_REMOVE_IDS, []) if x != int(item_id)
                ]
                request.session.modified = True
            return redirect(_detail_url(batch.id, edit=True))

        if action == "stage_remove_return":
            label_code = (request.POST.get("label_code") or "").strip()
            if label_code:
                s = set(request.session.get(SK_REMOVE_RET_LABELS, []))
                s.add(label_code)
                request.session[SK_REMOVE_RET_LABELS] = sorted(s)
                request.session.modified = True
            return redirect(_detail_url(batch.id, edit=True))

        if action == "undo_remove_return":
            label_code = (request.POST.get("label_code") or "").strip()
            request.session[SK_REMOVE_RET_LABELS] = [
                x for x in request.session.get(SK_REMOVE_RET_LABELS, []) if x != label_code
            ]
            request.session.modified = True
            return redirect(_detail_url(batch.id, edit=True))

        if action == "confirm_save":
            staged_text = (request.session.get(SK_ADD_TEXT) or "").strip()
            codes = _parse_lines(staged_text)

            staged_remove_ids = request.session.get(SK_REMOVE_IDS, []) or []
            staged_remove_ret_labels = request.session.get(SK_REMOVE_RET_LABELS, []) or []

            normal_codes = [c for c in codes if not _normalize_ret_code(c)]
            ret_inputs = [c for c in codes if _normalize_ret_code(c)]

            normal_added = 0
            return_added = 0
            removed_count = 0

            with transaction.atomic():
                if staged_remove_ids:
                    rm_qs = PPDeliveryItem.objects.filter(batch=batch, id__in=staged_remove_ids).select_related("order")
                    rm_order_ids = [it.order_id for it in rm_qs if it.order_id]
                    removed_count += rm_qs.count()
                    rm_qs.delete()
                    _reset_order_status_if_removed(rm_order_ids, user=request.user, to_status="INBOUND")

                if staged_remove_ret_labels:
                    rm_ret_qs = PPDeliveryItem.objects.filter(
                        batch=batch,
                        source_type=PPDeliveryItem.SOURCE_RETURN,
                        source_code__in=staged_remove_ret_labels,
                    ).select_related("order")
                    rm_ret_order_ids = [it.order_id for it in rm_ret_qs if it.order_id]
                    removed_count += rm_ret_qs.count()
                    rm_ret_qs.delete()
                    _reset_return_order_status_if_removed(rm_ret_order_ids, user=request.user)

                    cur_labels = set(_batch_get_label_codes(batch))
                    cur_labels.difference_update(staged_remove_ret_labels)
                    _batch_set_label_codes(batch, sorted(cur_labels))

                    remaining_mids = set()
                    for lc in cur_labels:
                        p = _ret_parts(lc)
                        if p:
                            remaining_mids.add(int(p[1]))
                    _batch_set_master_ids(batch, sorted(remaining_mids))
                    batch.save()

                normal_to_update: List[Order] = []
                if normal_codes:
                    found_orders = list(Order.objects.filter(tracking_no__in=normal_codes))
                    for o in found_orders:
                        if not _order_is_allowed_for_pp(o):
                            continue
                        if _order_is_in_any_return_batch(o):
                            continue
                        if _order_is_in_any_pp(o, exclude_batch_id=batch.id):
                            continue

                        _it, created = PPDeliveryItem.objects.get_or_create(
                            batch=batch,
                            order=o,
                            defaults={"source_type": PPDeliveryItem.SOURCE_NORMAL, "source_code": ""},
                        )
                        if created:
                            normal_added += 1
                            normal_to_update.append(o)

                return_to_update: List[Order] = []
                if ret_inputs and ReturnBatch and ReturnLabel and ReturnLabelItem:
                    ret_orders, mids, lcodes, _ = _collect_return_orders_for_pp(
                        ret_codes=ret_inputs,
                        ReturnBatch=ReturnBatch,
                        ReturnLabel=ReturnLabel,
                        ReturnLabelItem=ReturnLabelItem,
                        exclude_batch_id=batch.id,
                    )

                    if mids:
                        cur_mids = set(_batch_get_master_ids(batch))
                        cur_mids.update(int(x) for x in mids)
                        _batch_set_master_ids(batch, sorted(cur_mids))
                    if lcodes:
                        cur_labels = set(_batch_get_label_codes(batch))
                        cur_labels.update(lcodes)
                        _batch_set_label_codes(batch, sorted(cur_labels))
                    if mids or lcodes:
                        batch.save()

                    uniq: Dict[int, Tuple[Order, str]] = {}
                    for o, sc in ret_orders:
                        uniq[o.id] = (o, sc)

                    for o, sc in uniq.values():
                        _it, created = PPDeliveryItem.objects.get_or_create(
                            batch=batch,
                            order=o,
                            defaults={"source_type": PPDeliveryItem.SOURCE_RETURN, "source_code": sc},
                        )
                        if created:
                            return_added += 1
                            return_to_update.append(o)

                posted_shipper_id = (request.POST.get("shipper_id") or "").strip()
                selected_shipper = getattr(batch, "shipper", None)
                if posted_shipper_id.isdigit():
                    selected_shipper = Shipper.objects.filter(id=int(posted_shipper_id)).first()
                    if selected_shipper and getattr(batch, "shipper_id", None) != selected_shipper.id:
                        batch.shipper = selected_shipper
                        batch.save(update_fields=["shipper"])

                _set_order_status_after_pp_assign(
                    normal_to_update,
                    is_return=False,
                    shipper=selected_shipper,
                    user=request.user,
                )
                _set_order_status_after_pp_assign(
                    return_to_update,
                    is_return=True,
                    shipper=selected_shipper,
                    user=request.user,
                )
                _safe_recalc_batch_totals(batch, save=True)

            _clear_edit_session()
            messages.success(request, f"Saved. Added {normal_added + return_added}, Removed {removed_count}.")
            return redirect(_detail_url(batch.id, edit=False))

        return redirect(_detail_url(batch.id, edit=False))

    return_order_ids = list(
        batch.items.filter(source_type=PPDeliveryItem.SOURCE_RETURN).values_list("order_id", flat=True)
    )

    items = (
        batch.items
        .select_related("order", "order__seller")
        .filter(source_type=PPDeliveryItem.SOURCE_NORMAL)
        .exclude(order_id__in=return_order_ids)
        .order_by("id")
    )

    staged_remove = set(request.session.get(SK_REMOVE_IDS, []))
    staged_add_text = request.session.get(SK_ADD_TEXT, "") or ""
    staged_remove_ret_labels = set(request.session.get(SK_REMOVE_RET_LABELS, []))

    preview_rows = []
    preview_notfound = []
    preview_return = []

    raw_codes = _parse_lines(staged_add_text)
    if raw_codes:
        normal_codes = [c for c in raw_codes if not _normalize_ret_code(c)]
        if normal_codes:
            found_qs = Order.objects.filter(tracking_no__in=normal_codes).select_related("seller")
            found_map = {o.tracking_no: o for o in found_qs}
            for code in normal_codes:
                o = found_map.get(code)
                if not o:
                    preview_notfound.append(code)
                    continue

                ok = True
                why = "OK"
                if not _order_is_allowed_for_pp(o):
                    ok = False
                    why = f"Status {o.status} not allowed"
                elif _order_is_in_any_return_batch(o):
                    ok = False
                    why = "Already in Return batch"
                elif _order_is_in_any_pp(o, exclude_batch_id=batch.id):
                    ok = False
                    why = "Already in PP batch"

                preview_rows.append({"order": o, "ok": ok, "why": why})

        ret_inputs = [c for c in raw_codes if _normalize_ret_code(c)]
        if ret_inputs:
            if ReturnBatch and ReturnLabel and ReturnLabelItem:
                _r, _m, _l, code_status = _collect_return_orders_for_pp(
                    ret_codes=ret_inputs,
                    ReturnBatch=ReturnBatch,
                    ReturnLabel=ReturnLabel,
                    ReturnLabelItem=ReturnLabelItem,
                    exclude_batch_id=batch.id,
                )
                for code, st in sorted(code_status.items()):
                    preview_return.append({"code": code, "status": st})
            else:
                for c in ret_inputs:
                    preview_return.append({"code": c, "status": "Return models not ready"})

    return_blocks = []
    if ReturnBatch and ReturnLabel and ReturnLabelItem:
        for sc in (_batch_get_label_codes(batch) or []):
            p = _ret_parts(sc)
            if not p:
                continue
            prefix, master_id, label_id = p
            if not label_id:
                continue

            rb = ReturnBatch.objects.filter(id=master_id).first()
            lb = ReturnLabel.objects.filter(id=label_id, batch_id=master_id).first()
            if not (rb and lb):
                continue

            pc = ReturnLabelItem.objects.filter(label_id=lb.id).count()
            label_code = f"{prefix}-{master_id}-{label_id}"
            return_blocks.append({
                "code": label_code,
                "label_id": lb.id,
                "label_code": label_code,
                "batch": rb,
                "pc": pc,
                "cod": getattr(lb, "cod_amount", 0),
                "shop": getattr(lb, "shop_name", ""),
                "address": getattr(lb, "ship_to_address", ""),
                "phone": getattr(lb, "ship_to_phone", ""),
                "staged_removed": label_code in staged_remove_ret_labels,
            })

    total_shipment = items.count()
    total_return_batch = len(set(_batch_get_master_ids(batch)))
    total_all = int(total_shipment) + int(total_return_batch)

    return render(request, "deliverpp/detail.html", {
        "batch": batch,
        "edit_mode": edit_mode,
        "items": items,
        "staged_remove": staged_remove,
        "staged_remove_ret_labels": staged_remove_ret_labels,
        "staged_add_text": staged_add_text,
        "preview_rows": preview_rows,
        "preview_notfound": preview_notfound,
        "preview_return": preview_return,
        "return_blocks": return_blocks,
        "total_shipment": total_shipment,
        "total_return_batch": total_return_batch,
        "total_all": total_all,
        "now": timezone.now(),
        "shippers": _get_pp_shippers(),
    })


@login_required
def pp_delivery_print(request, batch_id: int):
    batch = get_object_or_404(PPDeliveryBatch, id=batch_id)

    items = (
        batch.items.select_related("order", "order__seller")
        .filter(source_type=PPDeliveryItem.SOURCE_NORMAL)
        .order_by("id")
    )

    return_blocks = []
    ReturnBatch, ReturnBatchItem, ReturnLabel, ReturnLabelItem = get_return_models()
    if ReturnBatch and ReturnLabel and ReturnLabelItem:
        for sc in (_batch_get_label_codes(batch) or []):
            p = _ret_parts(sc)
            if not p:
                continue
            prefix, master_id, label_id = p
            if not label_id:
                continue

            rb = ReturnBatch.objects.filter(id=master_id).first()
            lb = ReturnLabel.objects.filter(id=label_id, batch_id=master_id).first()
            if not (rb and lb):
                continue

            pc = ReturnLabelItem.objects.filter(label_id=lb.id).count()
            return_blocks.append({
                "code": f"{prefix}-{master_id}-{label_id}",
                "created_at": getattr(rb, "created_at", None),
                "shop": getattr(lb, "shop_name", "") or "",
                "address": getattr(lb, "ship_to_address", "") or "",
                "phone": getattr(lb, "ship_to_phone", "") or "",
                "pc": pc,
                "cod": getattr(lb, "cod_amount", 0) or 0,
            })

    return render(request, "deliverpp/print_list.html", {
        "batch": batch,
        "items": items,
        "return_blocks": return_blocks,
    })