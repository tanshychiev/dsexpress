from __future__ import annotations

import datetime
import json
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Exists, OuterRef
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from masterdata.models import Shipper
from orders.activity import add_order_activity
from orders.audit import add_audit_log
from orders.models import AuditLog, Order, OrderActivity
from .models import ClearPPCOD, PPDeliveryBatch, PPDeliveryItem, SystemSetting


# =========================================================
# HELPERS
# =========================================================
def _d(v) -> Decimal:
    try:
        return Decimal(str(v or "0")).quantize(Decimal("0.00"))
    except Exception:
        return Decimal("0.00")


def _stage1_done(batch: PPDeliveryBatch) -> bool:
    return PPDeliveryItem.objects.filter(
        batch=batch,
        delivery_cleared_at__isnull=False,
    ).exists()


def _stage2_done(batch: PPDeliveryBatch) -> bool:
    return ClearPPCOD.objects.filter(
        batch=batch,
        finalized_at__isnull=False,
    ).exists()


def get_return_models():
    try:
        ReturnBatch = apps.get_model("returnshop", "ReturnShopBatch")
        ReturnBatchItem = apps.get_model("returnshop", "ReturnShopBatchItem")
        ReturnLabel = apps.get_model("returnshop", "ReturnShopLabel")
        ReturnLabelItem = apps.get_model("returnshop", "ReturnShopLabelItem")
        return ReturnBatch, ReturnBatchItem, ReturnLabel, ReturnLabelItem
    except Exception:
        return None, None, None, None


def _batch_get_label_codes(batch: PPDeliveryBatch) -> List[str]:
    val = (
        getattr(batch, "return_label_codes", None)
        or getattr(batch, "return_codes", None)
        or getattr(batch, "return_labels", None)
        or []
    )
    return list(val or [])


def _ret_parts(code: str) -> Optional[Tuple[str, int, Optional[int]]]:
    c = (code or "").strip().upper()
    if not c:
        return None

    parts = c.split("-")
    if len(parts) < 2:
        return None

    prefix = parts[0]
    if prefix not in ("RTS", "RET"):
        return None

    try:
        master_id = int(parts[1])
    except Exception:
        return None

    label_id = None
    if len(parts) >= 3 and str(parts[2]).isdigit():
        label_id = int(parts[2])

    return prefix, master_id, label_id


def _full_tracking(o: Order) -> str:
    for name in ("tracking_no", "tracking", "tracking_code", "code"):
        v = getattr(o, name, None)
        if v:
            return str(v)
    return str(getattr(o, "id", ""))


def _get_order_cod(o: Order) -> Decimal:
    for name in ("cod", "cod_amount", "cod_usd", "cod_value"):
        v = getattr(o, name, None)
        if v is not None:
            try:
                return Decimal(str(v or "0")).quantize(Decimal("0.00"))
            except Exception:
                pass
    return Decimal("0.00")


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


def _pick_cod_for_item(it: PPDeliveryItem) -> Decimal:
    o = getattr(it, "order", None)
    if not o:
        return Decimal("0.00")

    # Always use LIVE order COD
    return _get_order_cod(o)

def _ui_state(batch: PPDeliveryBatch, stage1_done: bool, stage2_done: bool) -> Tuple[str, str]:
    if batch.status == PPDeliveryBatch.STATUS_CANCELLED:
        return "CANCEL", "red"
    if stage2_done:
        return "CLEAR COD", "green"
    if stage1_done:
        return "CLEAR DELIVER", "blue"
    return "PENDING", "yellow"


def _save_order_and_logs(
    order: Order,
    *,
    user,
    new_status: str,
    note: str,
    action: str,
    new_shipper="__KEEP__",
    clear_done_at: bool = False,
    set_done_today: bool = False,
):
    old_status = order.status
    old_shipper = order.delivery_shipper

    order.status = new_status

    if new_shipper != "__KEEP__":
        order.delivery_shipper = new_shipper

    if clear_done_at:
        order.done_at = None
    elif set_done_today and not order.done_at:
        order.done_at = timezone.localdate()

    order.updated_at = timezone.now()
    order.updated_by = user
    order.save()

    add_order_activity(
        order=order,
        action=action,
        user=user,
        shipper=order.delivery_shipper or old_shipper,
        old_status=old_status,
        new_status=order.status,
        note=note,
    )

    add_audit_log(
        module=AuditLog.MODULE_ORDER,
        obj=order,
        action=AuditLog.ACTION_CHANGE_STATUS,
        user=user,
        field_name="status",
        old_value=old_status,
        new_value=order.status,
        note=note,
    )

    if old_shipper != order.delivery_shipper:
        add_audit_log(
            module=AuditLog.MODULE_ORDER,
            obj=order,
            action=AuditLog.ACTION_ASSIGN_SHIPPER,
            user=user,
            field_name="delivery_shipper",
            old_value=str(old_shipper.name if old_shipper else ""),
            new_value=str(order.delivery_shipper.name if order.delivery_shipper else ""),
            note="Delivery shipper changed during Clear PP",
        )


# =========================================================
# 1) CLEAR LIST
# =========================================================
@login_required
def clearpp_list(request: HttpRequest) -> HttpResponse:
    settings_obj = SystemSetting.get_solo()
    shippers = Shipper.objects.all().order_by("name")

    show = request.GET.get("show") == "1"
    assign_date_raw = (request.GET.get("assign_date") or "").strip()
    shipper_id_raw = (request.GET.get("shipper_id") or "").strip()
    batch_kw = (request.GET.get("batch_id") or "").strip()
    status = (request.GET.get("status") or "").strip()

    if not show:
        return render(request, "deliverpp/clearpp_list.html", {
            "settings_obj": settings_obj,
            "shippers": shippers,
            "rows": [],
            "show": False,
            "assign_date": "",
            "shipper_id": "",
            "batch_id": "",
            "status": "",
        })

    qs = (
        PPDeliveryBatch.objects
        .all()
        .select_related("shipper", "created_by")
        .order_by("-id")
    )

    if assign_date_raw and assign_date_raw.lower() not in ("mm/dd/yyyy", "dd/mm/yyyy"):
        d = parse_date(assign_date_raw)
        if not d:
            try:
                d = datetime.datetime.strptime(assign_date_raw, "%m/%d/%Y").date()
            except Exception:
                d = None
        if d:
            qs = qs.filter(assigned_at__date=d)

    if shipper_id_raw.isdigit():
        qs = qs.filter(shipper_id=int(shipper_id_raw))

    if batch_kw:
        if batch_kw.isdigit():
            qs = qs.filter(id=int(batch_kw))
        else:
            qs = qs.filter(code__icontains=batch_kw) | qs.filter(remark__icontains=batch_kw)

    qs = qs.annotate(
        _stage1_done=Exists(
            PPDeliveryItem.objects.filter(
                batch_id=OuterRef("pk"),
                delivery_cleared_at__isnull=False,
            )
        ),
        _stage2_done=Exists(
            ClearPPCOD.objects.filter(
                batch_id=OuterRef("pk"),
                finalized_at__isnull=False,
            )
        ),
        _pc_count=Count("items", distinct=True),
    )

    if status == "CANCEL":
        qs = qs.filter(status=PPDeliveryBatch.STATUS_CANCELLED)
    elif status == "DONE":
        qs = qs.filter(_stage2_done=True).exclude(status=PPDeliveryBatch.STATUS_CANCELLED)
    elif status == "PENDING":
        qs = qs.filter(_stage2_done=False).exclude(status=PPDeliveryBatch.STATUS_CANCELLED)

    rows = []
    for b in qs:
        b.total_pc = getattr(b, "_pc_count", 0) or 0
        label, color = _ui_state(b, b._stage1_done, b._stage2_done)

        if b.status == PPDeliveryBatch.STATUS_CANCELLED:
            batch_status = "CANCEL"
        elif b._stage2_done:
            batch_status = "DONE"
        else:
            batch_status = "PENDING"

        staff_name = "-"
        clear_cod = ClearPPCOD.objects.filter(batch=b).first()
        if clear_cod and clear_cod.finalized_by:
            staff_name = clear_cod.finalized_by.get_username()

        rows.append({
            "batch": b,
            "label": label,
            "color": color,
            "batch_status": batch_status,
            "staff_name": staff_name,
        })

    return render(request, "deliverpp/clearpp_list.html", {
        "settings_obj": settings_obj,
        "shippers": shippers,
        "rows": rows,
        "show": True,
        "assign_date": assign_date_raw,
        "shipper_id": shipper_id_raw,
        "batch_id": batch_kw,
        "status": status,
    })


# =========================================================
# 2) SETTINGS
# =========================================================
@login_required
def system_settings_view(request: HttpRequest) -> HttpResponse:
    settings_obj = SystemSetting.get_solo()

    if request.method == "POST":
        rate = request.POST.get("usd_to_khr_rate") or settings_obj.usd_to_khr_rate
        tol = request.POST.get("balance_tolerance_khr") or settings_obj.balance_tolerance_khr

        try:
            rate = int(rate)
        except Exception:
            rate = settings_obj.usd_to_khr_rate

        try:
            tol = int(tol)
        except Exception:
            tol = settings_obj.balance_tolerance_khr

        old_rate = settings_obj.usd_to_khr_rate
        old_tol = settings_obj.balance_tolerance_khr

        settings_obj.usd_to_khr_rate = max(1, rate)
        settings_obj.balance_tolerance_khr = max(0, tol)
        settings_obj.updated_by = request.user
        settings_obj.save()

        add_audit_log(
            module=AuditLog.MODULE_CLEAR_PP,
            obj=settings_obj,
            action=AuditLog.ACTION_UPDATE,
            user=request.user,
            old_value=json.dumps({
                "usd_to_khr_rate": old_rate,
                "balance_tolerance_khr": old_tol,
            }),
            new_value=json.dumps({
                "usd_to_khr_rate": settings_obj.usd_to_khr_rate,
                "balance_tolerance_khr": settings_obj.balance_tolerance_khr,
            }),
            note="Updated Clear PP system settings",
        )

        messages.success(request, "System settings updated.")
        return redirect("clearpp_settings")

    return render(request, "deliverpp/system_settings.html", {
        "settings_obj": settings_obj,
    })


# =========================================================
# 3) CLEAR DETAIL
# =========================================================
@login_required
def clearpp_detail(request: HttpRequest, batch_id: int) -> HttpResponse:
    batch = get_object_or_404(PPDeliveryBatch, id=batch_id)
    settings_obj = SystemSetting.get_solo()
    clear_cod_obj, _ = ClearPPCOD.objects.get_or_create(batch=batch)

    stage1_done = _stage1_done(batch)
    stage2_done = _stage2_done(batch)
    state_label, state_color = _ui_state(batch, stage1_done, stage2_done)

    items_qs = (
        PPDeliveryItem.objects
        .filter(batch=batch, source_type=PPDeliveryItem.SOURCE_NORMAL)
        .select_related("order", "order__seller")
        .order_by("id")
    )

    items: List[PPDeliveryItem] = []
    ticked_total_usd = Decimal("0.00")

    for it in items_qs:
        o = it.order
        o.full_tracking = _full_tracking(o)
        o.cod_display = _pick_cod_for_item(it)
        it.tick_locked = stage1_done

        if it.ticked:
            ticked_total_usd += Decimal(str(o.cod_display or "0"))

        items.append(it)

    ReturnBatch, ReturnBatchItem, ReturnLabel, ReturnLabelItem = get_return_models()
    label_codes = _batch_get_label_codes(batch) or []

    if not label_codes:
        tmp = (
            PPDeliveryItem.objects
            .filter(batch=batch, source_type=PPDeliveryItem.SOURCE_RETURN)
            .exclude(source_code__isnull=True)
            .exclude(source_code__exact="")
            .values_list("source_code", flat=True)
        )
        label_codes = sorted(set([x.strip() for x in tmp if x and x.strip()]))

    return_item_ids_map: Dict[str, List[int]] = {}
    if label_codes:
        qs_ret = (
            PPDeliveryItem.objects
            .filter(batch=batch, source_type=PPDeliveryItem.SOURCE_RETURN, source_code__in=label_codes)
            .values_list("source_code", "id")
        )
        for sc, iid in qs_ret:
            if sc:
                return_item_ids_map.setdefault(sc.strip(), []).append(int(iid))

    return_blocks: List[dict] = []
    for sc in label_codes:
        p = _ret_parts(sc)
        if not p:
            continue

        _prefix, master_id, label_id = p
        rb = ReturnBatch.objects.filter(id=master_id).first() if ReturnBatch else None
        lb = ReturnLabel.objects.filter(id=label_id, batch_id=master_id).first() if (ReturnLabel and label_id) else None

        pc = 0
        if ReturnLabelItem and lb:
            try:
                pc = ReturnLabelItem.objects.filter(label_id=lb.id).count()
            except Exception:
                pc = 0

        cod_val = _get_return_label_cod(lb) if lb else Decimal("0.00")
        item_ids = return_item_ids_map.get(sc, [])
        ticked_count = PPDeliveryItem.objects.filter(id__in=item_ids, ticked=True).count() if item_ids else 0

        return_blocks.append({
            "code": sc,
            "label_id": getattr(lb, "id", None),
            "created_at": getattr(rb, "created_at", None),
            "seller": getattr(lb, "shop_name", "") if lb else "",
            "location": getattr(lb, "ship_to_address", "") if lb else "",
            "phone": getattr(lb, "ship_to_phone", "") if lb else "",
            "total_pc": pc,
            "cod": cod_val,
            "item_ids": item_ids,
            "ticked_all": bool(item_ids) and ticked_count == len(item_ids),
            "tick_locked": stage1_done,
        })

    total_shipment = items_qs.count()
    total_return_batch = len(return_blocks)
    total_all = total_shipment + total_return_batch

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save_reason_one":
            item_id = (request.POST.get("item_id") or "").strip()
            reason_value = (request.POST.get("reason_value") or "").strip()

            if item_id.isdigit():
                it = (
                    PPDeliveryItem.objects
                    .filter(batch=batch, id=int(item_id))
                    .select_related("order")
                    .first()
                )
                if it:
                    old_reason = it.reason or ""
                    it.reason = reason_value
                    it.save(update_fields=["reason"])

                    if it.source_type == PPDeliveryItem.SOURCE_NORMAL and it.order_id:
                        it.order.reason = it.reason
                        it.order.updated_at = timezone.now()
                        it.order.updated_by = request.user
                        it.order.save(update_fields=["reason", "updated_at", "updated_by"])

                        add_audit_log(
                            module=AuditLog.MODULE_ORDER,
                            obj=it.order,
                            action=AuditLog.ACTION_UPDATE,
                            user=request.user,
                            field_name="reason",
                            old_value=old_reason,
                            new_value=reason_value,
                            note="Updated reason from Clear PP detail",
                        )

                        add_order_activity(
                            order=it.order,
                            action=OrderActivity.ACTION_EDIT,
                            user=request.user,
                            shipper=it.order.delivery_shipper,
                            old_status=it.order.status,
                            new_status=it.order.status,
                            note=f"Reason updated from Clear PP: {old_reason or '-'} -> {reason_value or '-'}",
                        )

            messages.success(request, "Saved reason.")
            return redirect("clearpp_detail", batch_id=batch.id)

        if action in ("clear_cod", "finalize_cod"):
            if batch.status == PPDeliveryBatch.STATUS_CANCELLED:
                messages.error(request, "Batch is CANCELLED.")
                return redirect("clearpp_detail", batch_id=batch.id)

            if not stage1_done:
                messages.error(request, "Please Clear Delivery first.")
                return redirect("clearpp_detail", batch_id=batch.id)

            rate = Decimal(str(settings_obj.usd_to_khr_rate))
            tol = int(settings_obj.balance_tolerance_khr)

            cash_usd = _d(request.POST.get("cash_usd"))
            cash_khr = Decimal(str(request.POST.get("cash_khr") or "0"))
            aba_usd = _d(request.POST.get("aba_usd"))
            aba_khr = Decimal(str(request.POST.get("aba_khr") or "0"))
            expense = _d(request.POST.get("expense"))
            note = (request.POST.get("note") or "").strip()

            ticked_items = (
                PPDeliveryItem.objects
                .filter(batch=batch, ticked=True)
                .select_related("order")
            )

            target_total = Decimal("0.00")
            for it in ticked_items:
                if it.source_type == PPDeliveryItem.SOURCE_NORMAL:
                    target_total += _pick_cod_for_item(it)

            actual_total = (cash_usd + aba_usd) + ((cash_khr + aba_khr) / rate) - expense
            diff_khr = int((actual_total - target_total) * rate)
            allow_finalize = (abs(diff_khr) <= tol) or (note != "")

            old_clear_snapshot = {
                "cash_usd": str(clear_cod_obj.cash_usd or 0),
                "cash_khr": int(clear_cod_obj.cash_khr or 0),
                "aba_usd": str(clear_cod_obj.aba_usd or 0),
                "aba_khr": int(clear_cod_obj.aba_khr or 0),
                "expense": str(clear_cod_obj.expense or 0),
                "note": clear_cod_obj.note or "",
                "target_total_usd": str(clear_cod_obj.target_total_usd or 0),
                "input_total_usd": str(clear_cod_obj.input_total_usd or 0),
                "diff_khr": int(clear_cod_obj.diff_khr or 0),
                "is_balanced": bool(clear_cod_obj.is_balanced),
                "finalized_at": str(clear_cod_obj.finalized_at or ""),
            }

            clear_cod_obj.cash_usd = cash_usd
            clear_cod_obj.cash_khr = int(cash_khr)
            clear_cod_obj.aba_usd = aba_usd
            clear_cod_obj.aba_khr = int(aba_khr)
            clear_cod_obj.expense = expense
            clear_cod_obj.note = note
            clear_cod_obj.target_total_usd = target_total
            clear_cod_obj.input_total_usd = actual_total
            clear_cod_obj.diff_khr = diff_khr
            clear_cod_obj.is_balanced = bool(abs(diff_khr) <= tol)

            if allow_finalize:
                now = timezone.now()
                clear_cod_obj.finalized_by = request.user
                clear_cod_obj.finalized_at = now

                PPDeliveryItem.objects.filter(batch=batch).update(cod_cleared_at=now)

                # ===== NORMAL UNTICKED => INBOUND =====
                unticked_normal_orders = list(
                    Order.objects.filter(
                        pp_items__batch=batch,
                        pp_items__source_type=PPDeliveryItem.SOURCE_NORMAL,
                        pp_items__ticked=False,
                    ).distinct()
                )

                for order in unticked_normal_orders:
                    _save_order_and_logs(
                        order,
                        user=request.user,
                        new_status=Order.STATUS_INBOUND,
                        note=f"Clear COD finalized: normal order not ticked, reset to inbound from batch {batch.code}",
                        action=OrderActivity.ACTION_INBOUND,
                        new_shipper=None,
                        clear_done_at=True,
                    )

                # ===== NORMAL TICKED => DELIVERED =====
                ticked_normal_orders = list(
                    Order.objects.filter(
                        pp_items__batch=batch,
                        pp_items__source_type=PPDeliveryItem.SOURCE_NORMAL,
                        pp_items__ticked=True,
                    ).distinct()
                )

                for order in ticked_normal_orders:
                    _save_order_and_logs(
                        order,
                        user=request.user,
                        new_status=Order.STATUS_DELIVERED,
                        note=f"Clear COD finalized: delivered from batch {batch.code}",
                        action=OrderActivity.ACTION_DELIVERED,
                        new_shipper="__KEEP__",
                        set_done_today=False,
                    )

                # ===== RETURN UNTICKED => RETURN_ASSIGNED =====
                unticked_return_orders = list(
                    Order.objects.filter(
                        pp_items__batch=batch,
                        pp_items__source_type=PPDeliveryItem.SOURCE_RETURN,
                        pp_items__ticked=False,
                    ).distinct()
                )

                for order in unticked_return_orders:
                    _save_order_and_logs(
                        order,
                        user=request.user,
                        new_status=Order.STATUS_RETURN_ASSIGNED,
                        note=f"Clear COD finalized: return order not ticked, back to return assigned from batch {batch.code}",
                        action=OrderActivity.ACTION_EDIT,
                        new_shipper=None,
                        clear_done_at=True,
                    )

                # ===== RETURN TICKED => RETURNED =====
                ticked_return_orders = list(
                    Order.objects.filter(
                        pp_items__batch=batch,
                        pp_items__source_type=PPDeliveryItem.SOURCE_RETURN,
                        pp_items__ticked=True,
                    ).distinct()
                )

                for order in ticked_return_orders:
                    _save_order_and_logs(
                        order,
                        user=request.user,
                        new_status=Order.STATUS_RETURNED,
                        note=f"Clear COD finalized: return completed from batch {batch.code}",
                        action=getattr(OrderActivity, "ACTION_RETURNED", OrderActivity.ACTION_EDIT),
                        new_shipper="__KEEP__",
                        set_done_today=False,
                    )

                old_batch_status = batch.status
                batch.status = PPDeliveryBatch.STATUS_DONE
                batch.save(update_fields=["status"])

                add_audit_log(
                    module=AuditLog.MODULE_CLEAR_PP,
                    obj=batch,
                    action=AuditLog.ACTION_UPDATE,
                    user=request.user,
                    field_name="status",
                    old_value=old_batch_status,
                    new_value=batch.status,
                    note="Batch marked DONE during Clear COD finalize",
                )

            clear_cod_obj.save()

            add_audit_log(
                module=AuditLog.MODULE_CLEAR_PP,
                obj=clear_cod_obj,
                action=AuditLog.ACTION_UPDATE,
                user=request.user,
                old_value=json.dumps(old_clear_snapshot),
                new_value=json.dumps({
                    "cash_usd": str(clear_cod_obj.cash_usd or 0),
                    "cash_khr": int(clear_cod_obj.cash_khr or 0),
                    "aba_usd": str(clear_cod_obj.aba_usd or 0),
                    "aba_khr": int(clear_cod_obj.aba_khr or 0),
                    "expense": str(clear_cod_obj.expense or 0),
                    "note": clear_cod_obj.note or "",
                    "target_total_usd": str(clear_cod_obj.target_total_usd or 0),
                    "input_total_usd": str(clear_cod_obj.input_total_usd or 0),
                    "diff_khr": int(clear_cod_obj.diff_khr or 0),
                    "is_balanced": bool(clear_cod_obj.is_balanced),
                    "finalized_at": str(clear_cod_obj.finalized_at or ""),
                }),
                note=f"Clear COD saved for batch {batch.code}",
            )

            if allow_finalize:
                messages.success(request, "Clear COD saved.")
            else:
                messages.error(request, "Not balanced. Please fill NOTE to allow submit.")

            return redirect("clearpp_detail", batch_id=batch.id)

    return render(request, "deliverpp/clearpp_detail.html", {
        "batch": batch,
        "settings_obj": settings_obj,
        "items": items,
        "return_groups": return_blocks,
        "total_shipment": total_shipment,
        "total_return_batch": total_return_batch,
        "total_all": total_all,
        "stage1_done": stage1_done,
        "stage2_done": stage2_done,
        "ticked_total_usd": ticked_total_usd,
        "cod_form": clear_cod_obj,
        "state_label": state_label,
        "state_color": state_color,
        "toggle_tick_url": reverse("clearpp_toggle_tick", args=[batch.id]),
        "set_tick_many_url": reverse("clearpp_set_tick_many", args=[batch.id]),
        "clear_delivery_url": reverse("clearpp_clear_delivery", args=[batch.id]),
        "undo_clear_url": reverse("clearpp_undo_clear", args=[batch.id]),
        "cancel_url": reverse("clearpp_cancel", args=[batch.id]),
    })


# =========================================================
# 4) AJAX: TOGGLE TICK
# =========================================================
@login_required
def clearpp_toggle_tick(request: HttpRequest, batch_id: int) -> JsonResponse:
    batch = get_object_or_404(PPDeliveryBatch, id=batch_id)

    if _stage1_done(batch):
        return JsonResponse({"ok": False, "error": "Tick is locked after Clear Delivery."}, status=400)

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        payload = {}

    item_id = payload.get("item_id")
    ticked = payload.get("ticked", None)

    if not str(item_id).isdigit():
        return JsonResponse({"ok": False, "error": "Invalid item_id"}, status=400)

    it = get_object_or_404(PPDeliveryItem, id=int(item_id), batch=batch)
    it.ticked = (not it.ticked) if ticked is None else bool(ticked)
    it.save(update_fields=["ticked"])

    return JsonResponse({"ok": True, "ticked": it.ticked})


# =========================================================
# 4B) AJAX: TICK MANY
# =========================================================
@login_required
def clearpp_set_tick_many(request: HttpRequest, batch_id: int) -> JsonResponse:
    batch = get_object_or_404(PPDeliveryBatch, id=batch_id)

    if _stage1_done(batch):
        return JsonResponse({"ok": False, "error": "Tick is locked after Clear Delivery."}, status=400)

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        payload = {}

    item_ids = payload.get("item_ids") or []
    ticked = payload.get("ticked", True)

    clean_ids: List[int] = []
    for x in item_ids:
        try:
            clean_ids.append(int(x))
        except Exception:
            pass

    if not clean_ids:
        return JsonResponse({"ok": False, "error": "No item ids"}, status=400)

    updated = PPDeliveryItem.objects.filter(
        batch=batch,
        id__in=clean_ids,
    ).update(ticked=bool(ticked))

    return JsonResponse({"ok": True, "updated": updated, "ticked": bool(ticked)})


# =========================================================
# 5) AJAX: CLEAR DELIVERY
# =========================================================
@login_required
def clear_delivery_ajax(request: HttpRequest, batch_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    batch = get_object_or_404(PPDeliveryBatch, id=batch_id)
    if batch.status == PPDeliveryBatch.STATUS_CANCELLED:
        return JsonResponse({"ok": False, "error": "Batch is CANCELLED"}, status=400)

    now = timezone.now()

    with transaction.atomic():
        ticked_items = PPDeliveryItem.objects.filter(
            batch=batch,
            ticked=True,
        )

        ticked_items.update(delivery_cleared_at=now)

        # ===== NORMAL: ticked => DELIVERED =====
        ticked_normal_orders = list(
            Order.objects.filter(
                pp_items__batch=batch,
                pp_items__ticked=True,
                pp_items__source_type=PPDeliveryItem.SOURCE_NORMAL,
            ).distinct()
        )

        for order in ticked_normal_orders:
            _save_order_and_logs(
                order,
                user=request.user,
                new_status=Order.STATUS_DELIVERED,
                note=f"Clear Delivery ticked normal from batch {batch.code}",
                action=OrderActivity.ACTION_DELIVERED,
                new_shipper="__KEEP__",
                set_done_today=True,
            )

        # ===== RETURN: ticked => RETURNED =====
        ticked_return_orders = list(
            Order.objects.filter(
                pp_items__batch=batch,
                pp_items__ticked=True,
                pp_items__source_type=PPDeliveryItem.SOURCE_RETURN,
            ).distinct()
        )

        for order in ticked_return_orders:
            _save_order_and_logs(
                order,
                user=request.user,
                new_status=Order.STATUS_RETURNED,
                note=f"Clear Delivery ticked return from batch {batch.code}",
                action=getattr(OrderActivity, "ACTION_RETURNED", OrderActivity.ACTION_EDIT),
                new_shipper="__KEEP__",
                set_done_today=True,
            )

        # ===== RETURN: unticked stays RETURNING =====
        unticked_return_orders = list(
            Order.objects.filter(
                pp_items__batch=batch,
                pp_items__ticked=False,
                pp_items__source_type=PPDeliveryItem.SOURCE_RETURN,
            ).distinct()
        )

        for order in unticked_return_orders:
            _save_order_and_logs(
                order,
                user=request.user,
                new_status=Order.STATUS_RETURNING,
                note=f"Clear Delivery unticked return remains returning in batch {batch.code}",
                action=OrderActivity.ACTION_EDIT,
                new_shipper="__KEEP__",
                clear_done_at=True,
            )

        add_audit_log(
            module=AuditLog.MODULE_CLEAR_PP,
            obj=batch,
            action=AuditLog.ACTION_UPDATE,
            user=request.user,
            note=f"Clear Delivery executed for batch {batch.code}",
        )

    return JsonResponse({"ok": True})


# =========================================================
# 6) AJAX: UNDO
# =========================================================
@login_required
def clearpp_undo_clear(request: HttpRequest, batch_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    batch = get_object_or_404(PPDeliveryBatch, id=batch_id)

    with transaction.atomic():
        PPDeliveryItem.objects.filter(batch=batch).update(
            ticked=False,
            delivery_cleared_at=None,
            cod_cleared_at=None,
        )

        # ===== NORMAL => OUT_FOR_DELIVERY =====
        normal_orders = list(
            Order.objects.filter(
                pp_items__batch=batch,
                pp_items__source_type=PPDeliveryItem.SOURCE_NORMAL,
            ).distinct()
        )

        for order in normal_orders:
            _save_order_and_logs(
                order,
                user=request.user,
                new_status=Order.STATUS_OUT_FOR_DELIVERY,
                note=f"Undo Clear PP normal from batch {batch.code}",
                action=OrderActivity.ACTION_OUT_FOR_DELIVERY,
                new_shipper="__KEEP__",
                clear_done_at=True,
            )

        # ===== RETURN => RETURNING =====
        return_orders = list(
            Order.objects.filter(
                pp_items__batch=batch,
                pp_items__source_type=PPDeliveryItem.SOURCE_RETURN,
            ).distinct()
        )

        for order in return_orders:
            _save_order_and_logs(
                order,
                user=request.user,
                new_status=Order.STATUS_RETURNING,
                note=f"Undo Clear PP return from batch {batch.code}",
                action=OrderActivity.ACTION_EDIT,
                new_shipper="__KEEP__",
                clear_done_at=True,
            )

        clear_cod = ClearPPCOD.objects.filter(batch=batch).first()
        if clear_cod:
            old_snapshot = {
                "finalized_at": str(clear_cod.finalized_at or ""),
                "finalized_by": str(clear_cod.finalized_by_id or ""),
                "is_balanced": bool(clear_cod.is_balanced),
                "diff_khr": int(clear_cod.diff_khr or 0),
            }

            clear_cod.finalized_at = None
            clear_cod.finalized_by = None
            clear_cod.is_balanced = False
            clear_cod.diff_khr = 0
            clear_cod.save()

            add_audit_log(
                module=AuditLog.MODULE_CLEAR_PP,
                obj=clear_cod,
                action=AuditLog.ACTION_UPDATE,
                user=request.user,
                old_value=json.dumps(old_snapshot),
                new_value=json.dumps({
                    "finalized_at": "",
                    "finalized_by": "",
                    "is_balanced": False,
                    "diff_khr": 0,
                }),
                note=f"Undo Clear COD for batch {batch.code}",
            )

        if batch.status in (PPDeliveryBatch.STATUS_CANCELLED, PPDeliveryBatch.STATUS_DONE):
            old_batch_status = batch.status
            batch.status = PPDeliveryBatch.STATUS_PENDING
            batch.save(update_fields=["status"])

            add_audit_log(
                module=AuditLog.MODULE_CLEAR_PP,
                obj=batch,
                action=AuditLog.ACTION_UPDATE,
                user=request.user,
                field_name="status",
                old_value=old_batch_status,
                new_value=batch.status,
                note="Undo clear changed batch status back to PENDING",
            )

    return JsonResponse({"ok": True})


# =========================================================
# 7) AJAX: CANCEL
# =========================================================
@login_required
def clearpp_cancel(request: HttpRequest, batch_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    batch = get_object_or_404(PPDeliveryBatch, id=batch_id)

    with transaction.atomic():
        item_qs = PPDeliveryItem.objects.filter(batch=batch)

        order_ids = list(
            item_qs.exclude(order_id__isnull=True)
            .values_list("order_id", flat=True)
            .distinct()
        )

        # ===== NORMAL => INBOUND =====
        normal_orders = list(
            Order.objects.filter(
                pp_items__batch=batch,
                pp_items__source_type=PPDeliveryItem.SOURCE_NORMAL,
            ).distinct()
        )

        for order in normal_orders:
            _save_order_and_logs(
                order,
                user=request.user,
                new_status=Order.STATUS_INBOUND,
                note=f"Batch {batch.code} cancelled from Clear PP",
                action=OrderActivity.ACTION_INBOUND,
                new_shipper="__KEEP__",
                clear_done_at=True,
            )

        # ===== RETURN => RETURN_ASSIGNED =====
        return_orders = list(
            Order.objects.filter(
                pp_items__batch=batch,
                pp_items__source_type=PPDeliveryItem.SOURCE_RETURN,
            ).distinct()
        )

        for order in return_orders:
            _save_order_and_logs(
                order,
                user=request.user,
                new_status=Order.STATUS_RETURN_ASSIGNED,
                note=f"Return batch {batch.code} cancelled from Clear PP",
                action=OrderActivity.ACTION_EDIT,
                new_shipper="__KEEP__",
                clear_done_at=True,
            )

        item_qs.update(
            ticked=False,
            delivery_cleared_at=None,
            cod_cleared_at=None,
        )

        clear_cod = ClearPPCOD.objects.filter(batch=batch).first()
        if clear_cod:
            old_snapshot = {
                "finalized_at": str(clear_cod.finalized_at or ""),
                "finalized_by": str(clear_cod.finalized_by_id or ""),
                "is_balanced": bool(clear_cod.is_balanced),
                "diff_khr": int(clear_cod.diff_khr or 0),
            }

            clear_cod.finalized_at = None
            clear_cod.finalized_by = None
            clear_cod.is_balanced = False
            clear_cod.diff_khr = 0
            clear_cod.save()

            add_audit_log(
                module=AuditLog.MODULE_CLEAR_PP,
                obj=clear_cod,
                action=AuditLog.ACTION_UPDATE,
                user=request.user,
                old_value=json.dumps(old_snapshot),
                new_value=json.dumps({
                    "finalized_at": "",
                    "finalized_by": "",
                    "is_balanced": False,
                    "diff_khr": 0,
                }),
                note=f"Clear COD reset because batch {batch.code} cancelled",
            )

        old_batch_status = batch.status
        batch.status = PPDeliveryBatch.STATUS_CANCELLED
        batch.save(update_fields=["status"])

        add_audit_log(
            module=AuditLog.MODULE_CLEAR_PP,
            obj=batch,
            action=AuditLog.ACTION_UPDATE,
            user=request.user,
            field_name="status",
            old_value=old_batch_status,
            new_value=batch.status,
            note=f"Batch {batch.code} cancelled",
        )

    return JsonResponse({"ok": True, "order_ids": order_ids})