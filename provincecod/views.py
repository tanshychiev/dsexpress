from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from masterdata.models import Seller, Shipper
from orders.models import Order, OrderActivity

from .excel import export_province_cod_report_xlsx
from .models import ProvinceCODBatch, ProvinceCODItem
from .services import (
    cancel_pending_batch,
    complete_batch_sent,
    mark_item_at_station,
    mark_item_delivery_issue,
    mark_item_out_for_delivery,
    mark_item_paid,
    mark_item_received,
    mark_item_return_received,
    mark_item_returning,
    mark_item_returned,
    mark_item_seller_settled,
    money,
    prepare_item_pricing,
    remove_pending_item,
    undo_seller_settlement,
)


ZERO = Decimal("0.00")

CALL_REASON_CHOICES = [
    ("NO_PICKUP", "No pickup / no answer"),
    ("ANSWERED", "Answered / picked up"),
    ("BUSY", "Phone busy"),
    ("PHONE_OFF", "Phone switched off"),
    ("WRONG_NUMBER", "Wrong phone number"),
    ("CALL_BACK", "Asked to call back later"),
    ("OTHER", "Other"),
]

CALL_REASON_LABELS = dict(CALL_REASON_CHOICES)


def _record_call_note(
    item,
    user,
    *,
    call_reason="",
    note="",
):
    """Record a call attempt without changing the Province COD status."""
    call_reason = (call_reason or "").strip().upper()

    if call_reason not in CALL_REASON_LABELS:
        raise ValueError("Please select a valid call reason.")

    reason_label = CALL_REASON_LABELS[call_reason]
    detail = (note or "").strip()

    actor_name = (
        user.get_full_name().strip()
        or user.get_username()
        or "User"
    )

    timestamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")

    call_entry = (
        f"[CALL {timestamp} | {actor_name}] "
        f"{reason_label}"
    )

    if detail:
        call_entry += f" — {detail}"

    old_note = (item.note or "").strip()
    item.note = (
        f"{call_entry}\n{old_note}"
        if old_note
        else call_entry
    )

    item.save(update_fields=["note", "updated_at"])


def _payment_received_datetime(raw_value):
    """Return an aware payment-received datetime from YYYY-MM-DD."""
    raw_value = (raw_value or "").strip()

    if not raw_value:
        return timezone.now()

    try:
        payment_date = datetime.strptime(
            raw_value,
            "%Y-%m-%d",
        ).date()
    except ValueError as exc:
        raise ValueError(
            "Payment received date must be a valid date."
        ) from exc

    current_local = timezone.localtime()
    naive_value = datetime.combine(
        payment_date,
        time(
            hour=current_local.hour,
            minute=current_local.minute,
            second=current_local.second,
        ),
    )

    return timezone.make_aware(
        naive_value,
        timezone.get_current_timezone(),
    )


def _mark_paid_from_report(
    item,
    user,
    *,
    paid_amount_raw="",
    payment_received_date="",
    carrier_reference="",
    note="",
):
    """Mark one report item as paid with an editable received amount/date."""
    raw_value = str(paid_amount_raw or "").strip()

    if raw_value:
        try:
            paid_amount = money(raw_value)
        except Exception as exc:
            raise ValueError(
                "Paid amount must be a valid number."
            ) from exc
    else:
        paid_amount = money(item.original_cod)

    if paid_amount < ZERO:
        raise ValueError("Paid amount cannot be negative.")

    difference = money(
        money(item.original_cod) - paid_amount
    )

    service_fee = difference if difference >= ZERO else ZERO

    mark_item_paid(
        item,
        user,
        carrier_fee=service_fee,
        carrier_reference=carrier_reference,
        note=note,
    )

    item.refresh_from_db()
    item.carrier_fee = difference
    item.paid_at = _payment_received_datetime(
        payment_received_date
    )

    item.save(
        update_fields=[
            "carrier_fee",
            "net_cod",
            "paid_at",
            "updated_at",
        ]
    )


def _scan_session_key():
    return "province_cod_scan_codes"


def _parse_codes(raw):
    output = []
    seen = set()

    for line in (raw or "").splitlines():
        code = (line or "").strip()

        if code and code not in seen:
            output.append(code)
            seen.add(code)

    return output


def _order_status(order):
    return str(
        getattr(order, "status", "") or ""
    ).upper().strip()


def _order_cod(order):
    return money(getattr(order, "cod", ZERO))


def _allowed_status(order):
    return _order_status(order) in {"CREATED", "INBOUND"}


def _active_carriers():
    return Shipper.objects.filter(
        is_active=True,
        shipper_type=Shipper.TYPE_PROVINCE,
    ).order_by("name")


def _order_detail_url(order):
    return f"/orders/created/{order.id}/"


def _existing_active_order_ids(order_ids):
    return set(
        ProvinceCODItem.objects.filter(
            order_id__in=order_ids,
            batch__status__in=[
                ProvinceCODBatch.STATUS_PENDING,
                ProvinceCODBatch.STATUS_SENT,
            ],
        ).values_list("order_id", flat=True)
    )


def _get_scanned_orders(codes):
    orders = list(
        Order.objects.filter(
            tracking_no__in=codes,
            is_deleted=False,
        ).select_related(
            "seller",
            "delivery_shipper",
        )
    )

    order_map = {
        order.tracking_no: order
        for order in orders
    }

    ordered = []
    not_found = []

    for code in codes:
        order = order_map.get(code)

        if order is None:
            not_found.append(code)
        else:
            ordered.append(order)

    active_ids = _existing_active_order_ids(
        [order.id for order in ordered]
    )

    allowed = []
    errors = []

    for order in ordered:
        if order.id in active_ids:
            reason = "Already in Province COD"
        elif not _allowed_status(order):
            reason = (
                f"Status {_order_status(order)} not allowed"
            )
        elif _order_cod(order) <= ZERO:
            reason = "No COD"
        else:
            allowed.append(order)
            continue

        errors.append((order, reason))

    return allowed, errors, not_found


def _row_for_order(order, error=""):
    seller = getattr(order, "seller", None)

    return {
        "id": order.id,
        "tracking_no": order.tracking_no,
        "tracking_url": _order_detail_url(order),
        "seller_name": (
            getattr(seller, "name", "-") or "-"
        ),
        "receiver_name": (
            getattr(order, "receiver_name", "") or "-"
        ),
        "receiver_phone": (
            getattr(order, "receiver_phone", "") or "-"
        ),
        "receiver_address": (
            getattr(order, "receiver_address", "") or "-"
        ),
        "cod": _order_cod(order),
        "status": _order_status(order) or "-",
        "error": error,
    }


@login_required
def batch_list(request):
    today = timezone.localdate()
    default_from = today - timedelta(days=6)

    date_from = (
        request.GET.get("date_from")
        or default_from.isoformat()
    ).strip()

    date_to = (
        request.GET.get("date_to")
        or today.isoformat()
    ).strip()

    status = (
        request.GET.get("status") or ""
    ).strip().upper()

    shipper_id = (
        request.GET.get("shipper") or ""
    ).strip()

    qs = (
        ProvinceCODBatch.objects
        .select_related(
            "shipper",
            "created_by",
            "sent_by",
        )
        .annotate(
            total_items=Count(
                "items",
                distinct=True,
            ),
            total_shops=Count(
                "items__order__seller",
                distinct=True,
            ),
            total_cod=Sum("items__original_cod"),
        )
        .order_by("-id")
    )

    if date_from:
        qs = qs.filter(
            created_at__date__gte=date_from
        )

    if date_to:
        qs = qs.filter(
            created_at__date__lte=date_to
        )

    if status:
        qs = qs.filter(status=status)

    if shipper_id.isdigit():
        qs = qs.filter(
            shipper_id=int(shipper_id)
        )

    return render(
        request,
        "provincecod/batch_list.html",
        {
            "rows": qs,
            "date_from": date_from,
            "date_to": date_to,
            "status": status,
            "shipper_id": shipper_id,
            "shippers": _active_carriers(),
        },
    )


@login_required
def batch_create(request):
    session_key = _scan_session_key()

    if request.method == "POST":
        action = (
            request.POST.get("action") or ""
        ).strip()

        if action == "scan_add":
            posted_codes = _parse_codes(
                request.POST.get(
                    "scan_codes",
                    "",
                )
            )

            if not posted_codes:
                messages.error(
                    request,
                    "Please scan tracking code(s).",
                )
                return redirect(
                    "provincecod:batch_create"
                )

            existing = list(
                request.session.get(
                    session_key,
                    [],
                )
            )
            seen = set(existing)
            added = 0

            for code in posted_codes:
                if code not in seen:
                    existing.append(code)
                    seen.add(code)
                    added += 1

            request.session[session_key] = existing
            request.session.modified = True

            messages.success(
                request,
                f"Added {added} tracking code(s).",
            )
            return redirect(
                "provincecod:batch_create"
            )

        if action == "scan_clear":
            request.session[session_key] = []
            request.session.modified = True

            messages.success(
                request,
                "Scanned list cleared.",
            )
            return redirect(
                "provincecod:batch_create"
            )

        if action == "remove_scan":
            code = (
                request.POST.get("code") or ""
            ).strip()

            existing = list(
                request.session.get(
                    session_key,
                    [],
                )
            )

            request.session[session_key] = [
                value
                for value in existing
                if value != code
            ]
            request.session.modified = True

            return redirect(
                "provincecod:batch_create"
            )

        if action in {
            "confirm_create",
            "confirm_sent",
        }:
            shipper_id = (
                request.POST.get("shipper_id")
                or ""
            ).strip()

            remark = (
                request.POST.get("remark")
                or ""
            ).strip()

            checked_ids = [
                int(value)
                for value in request.POST.getlist(
                    "checked_ids"
                )
                if str(value).isdigit()
            ]

            if not shipper_id.isdigit():
                messages.error(
                    request,
                    "Please select a carrier.",
                )
                return redirect(
                    "provincecod:batch_create"
                )

            shipper = (
                _active_carriers()
                .filter(pk=int(shipper_id))
                .first()
            )

            if not shipper:
                messages.error(
                    request,
                    "Selected carrier is invalid.",
                )
                return redirect(
                    "provincecod:batch_create"
                )

            scanned_codes = list(
                request.session.get(
                    session_key,
                    [],
                )
            )

            allowed_orders, _, _ = (
                _get_scanned_orders(
                    scanned_codes
                )
            )

            allowed_map = {
                order.id: order
                for order in allowed_orders
            }

            selected_orders = [
                allowed_map[order_id]
                for order_id in checked_ids
                if order_id in allowed_map
            ]

            if not selected_orders:
                messages.error(
                    request,
                    (
                        "Please tick at least one "
                        "allowed order."
                    ),
                )
                return redirect(
                    "provincecod:batch_create"
                )

            try:
                with transaction.atomic():
                    batch = (
                        ProvinceCODBatch.objects.create(
                            created_by=request.user,
                            shipper=shipper,
                            assigned_at=timezone.now(),
                            remark=remark,
                            status=(
                                ProvinceCODBatch
                                .STATUS_PENDING
                            ),
                        )
                    )

                    for order in selected_orders:
                        old_status = _order_status(
                            order
                        )

                        ProvinceCODItem.objects.create(
                            batch=batch,
                            order=order,
                            original_cod=_order_cod(
                                order
                            ),
                            status_before=old_status,
                        )

                        Order.objects.filter(
                            pk=order.pk
                        ).update(
                            status="PROCESSING",
                            delivery_shipper=shipper,
                            updated_at=timezone.now(),
                            updated_by=request.user,
                        )

                        OrderActivity.objects.create(
                            order=order,
                            action=(
                                "ASSIGN_PROVINCE_COD"
                            ),
                            old_status=old_status,
                            new_status="PROCESSING",
                            actor=request.user,
                            shipper=shipper,
                            note=(
                                "Assigned to Province "
                                "COD batch "
                                f"PVCOD-{batch.id}."
                            ),
                        )

                    complete_batch_sent(
                        batch,
                        request.user,
                    )

            except ValueError as exc:
                messages.error(
                    request,
                    str(exc),
                )
                return redirect(
                    "provincecod:batch_create"
                )

            request.session[session_key] = []
            request.session.modified = True

            messages.success(
                request,
                (
                    f"{len(selected_orders)} "
                    "Province COD order(s) "
                    "completed as sent in batch "
                    f"PVCOD-{batch.id}."
                ),
            )

            return redirect(
                "provincecod:batch_detail",
                pk=batch.id,
            )

    scanned_codes = list(
        request.session.get(
            session_key,
            [],
        )
    )

    allowed_orders, error_orders, not_found = (
        _get_scanned_orders(
            scanned_codes
        )
    )

    return render(
        request,
        "provincecod/batch_create.html",
        {
            "scan_value": "\n".join(
                scanned_codes
            ),
            "rows_allowed": [
                _row_for_order(order)
                for order in allowed_orders
            ],
            "rows_error": [
                _row_for_order(
                    order,
                    error=reason,
                )
                for order, reason
                in error_orders
            ],
            "not_found_codes": not_found,
            "total_count": len(scanned_codes),
            "found_count": len(
                allowed_orders
            ),
            "error_count": len(
                error_orders
            ),
            "not_found_count": len(
                not_found
            ),
            "shippers": _active_carriers(),
        },
    )


@login_required
def batch_detail(request, pk):
    batch = get_object_or_404(
        ProvinceCODBatch.objects.select_related(
            "shipper",
            "created_by",
            "sent_by",
            "cancelled_by",
        ),
        pk=pk,
    )

    if request.method == "POST":
        action = (
            request.POST.get("action") or ""
        ).strip()

        try:
            if action == "change_shipper":
                if (
                    batch.status
                    == ProvinceCODBatch.STATUS_CANCELLED
                ):
                    raise ValueError(
                        (
                            "A cancelled batch cannot "
                            "change carrier."
                        )
                    )

                shipper_id = (
                    request.POST.get(
                        "shipper_id"
                    )
                    or ""
                ).strip()

                if not shipper_id.isdigit():
                    raise ValueError(
                        "Please select a carrier."
                    )

                new_shipper = (
                    _active_carriers()
                    .filter(
                        pk=int(shipper_id)
                    )
                    .first()
                )

                if not new_shipper:
                    raise ValueError(
                        (
                            "Selected carrier is "
                            "invalid."
                        )
                    )

                has_locked_items = (
                    batch.items.filter(
                        Q(
                            cod_status=(
                                ProvinceCODItem
                                .STATUS_PAID
                            )
                        )
                        | Q(
                            seller_settled=True
                        )
                    ).exists()
                )

                if has_locked_items:
                    raise ValueError(
                        (
                            "Carrier cannot be changed "
                            "because this batch already "
                            "has a paid or seller-settled "
                            "order."
                        )
                    )

                old_shipper = batch.shipper
                old_shipper_name = (
                    getattr(
                        old_shipper,
                        "name",
                        "",
                    )
                    or "-"
                )

                with transaction.atomic():
                    locked_batch = (
                        ProvinceCODBatch.objects
                        .select_for_update()
                        .get(pk=batch.pk)
                    )

                    locked_batch.shipper = (
                        new_shipper
                    )
                    locked_batch.assigned_at = (
                        timezone.now()
                    )
                    locked_batch.save(
                        update_fields=[
                            "shipper",
                            "assigned_at",
                            "updated_at",
                        ]
                    )

                    items = list(
                        locked_batch.items
                        .select_for_update()
                        .select_related(
                            "order",
                            "order__seller",
                        )
                        .order_by("id")
                    )

                    for item in items:
                        prepare_item_pricing(
                            item
                        )

                        item.save(
                            update_fields=[
                                "province_fee",
                                "carrier_fixed_fee",
                                (
                                    "carrier_percent_"
                                    "rate"
                                ),
                                "updated_at",
                            ]
                        )

                        order = item.order
                        current_status = (
                            _order_status(order)
                        )

                        Order.objects.filter(
                            pk=order.pk
                        ).update(
                            delivery_shipper=(
                                new_shipper
                            ),
                            province_fee=(
                                item.province_fee
                            ),
                            updated_at=(
                                timezone.now()
                            ),
                            updated_by=(
                                request.user
                            ),
                        )

                        OrderActivity.objects.create(
                            order=order,
                            action=(
                                "CHANGE_PROVINCE_"
                                "COD_CARRIER"
                            ),
                            old_status=(
                                current_status
                            ),
                            new_status=(
                                current_status
                            ),
                            actor=request.user,
                            shipper=new_shipper,
                            note=(
                                "Province COD carrier "
                                "changed from "
                                f"{old_shipper_name} "
                                "to "
                                f"{new_shipper.name} "
                                "in "
                                f"PVCOD-"
                                f"{locked_batch.id}."
                            ),
                        )

                messages.success(
                    request,
                    (
                        "Carrier changed from "
                        f"{old_shipper_name} to "
                        f"{new_shipper.name}."
                    ),
                )

            elif action == "add_codes":
                if (
                    batch.status
                    != ProvinceCODBatch.STATUS_PENDING
                ):
                    raise ValueError(
                        (
                            "Only a pending batch "
                            "can be edited."
                        )
                    )

                codes = _parse_codes(
                    request.POST.get(
                        "scan_codes",
                        "",
                    )
                )

                if not codes:
                    raise ValueError(
                        (
                            "Please scan tracking "
                            "code(s)."
                        )
                    )

                (
                    allowed_orders,
                    error_orders,
                    not_found,
                ) = _get_scanned_orders(codes)

                existing_ids = set(
                    batch.items.values_list(
                        "order_id",
                        flat=True,
                    )
                )
                added = 0

                with transaction.atomic():
                    for order in allowed_orders:
                        if order.id in existing_ids:
                            continue

                        old_status = _order_status(
                            order
                        )

                        ProvinceCODItem.objects.create(
                            batch=batch,
                            order=order,
                            original_cod=_order_cod(
                                order
                            ),
                            status_before=old_status,
                        )

                        Order.objects.filter(
                            pk=order.pk
                        ).update(
                            status="PROCESSING",
                            delivery_shipper=(
                                batch.shipper
                            ),
                            updated_at=(
                                timezone.now()
                            ),
                            updated_by=(
                                request.user
                            ),
                        )

                        OrderActivity.objects.create(
                            order=order,
                            action=(
                                "ADD_TO_PROVINCE_COD"
                            ),
                            old_status=old_status,
                            new_status="PROCESSING",
                            actor=request.user,
                            shipper=batch.shipper,
                            note=(
                                "Added to Province COD "
                                "batch "
                                f"PVCOD-{batch.id}."
                            ),
                        )
                        added += 1

                messages.success(
                    request,
                    f"Added {added} order(s).",
                )

                if error_orders:
                    messages.warning(
                        request,
                        (
                            f"{len(error_orders)} "
                            "order(s) were not allowed."
                        ),
                    )

                if not_found:
                    messages.warning(
                        request,
                        (
                            f"{len(not_found)} tracking "
                            "code(s) were not found."
                        ),
                    )

            elif action == "remove_item":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get(
                        "item_id"
                    ),
                    batch=batch,
                )
                remove_pending_item(
                    item,
                    request.user,
                )
                messages.success(
                    request,
                    "Order removed from batch.",
                )

            elif action == "complete_sent":
                complete_batch_sent(
                    batch,
                    request.user,
                )
                messages.success(
                    request,
                    (
                        "Batch completed as SENT. "
                        "Orders are SENT in "
                        "Delivery Report."
                    ),
                )

            elif action == "cancel":
                cancel_pending_batch(
                    batch,
                    request.user,
                )
                messages.success(
                    request,
                    (
                        "Province COD batch "
                        "cancelled."
                    ),
                )

            elif action == "mark_at_station":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get("item_id"),
                    batch=batch,
                )
                mark_item_at_station(
                    item,
                    request.user,
                    note=request.POST.get("note", ""),
                )
                messages.success(
                    request,
                    "Item marked as AT STATION.",
                )

            elif action == "mark_out_for_delivery":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get("item_id"),
                    batch=batch,
                )
                mark_item_out_for_delivery(
                    item,
                    request.user,
                    note=request.POST.get("note", ""),
                )
                messages.success(
                    request,
                    "Item marked as OUT FOR DELIVERY.",
                )

            elif action == "mark_delivery_issue":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get("item_id"),
                    batch=batch,
                )
                mark_item_delivery_issue(
                    item,
                    request.user,
                    issue_reason=request.POST.get(
                        "issue_reason",
                        "",
                    ),
                    note=request.POST.get("note", ""),
                )
                messages.success(
                    request,
                    "Item marked as DELIVERY ISSUE.",
                )

            elif action == "mark_returning":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get("item_id"),
                    batch=batch,
                )
                mark_item_returning(
                    item,
                    request.user,
                    return_reason=request.POST.get(
                        "return_reason",
                        "",
                    ),
                    note=request.POST.get("note", ""),
                )
                messages.success(
                    request,
                    "Item marked as RETURNING.",
                )

            elif action == "mark_return_received":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get("item_id"),
                    batch=batch,
                )
                mark_item_return_received(
                    item,
                    request.user,
                    received_person=request.POST.get(
                        "received_person",
                        "",
                    ),
                    note=request.POST.get("note", ""),
                )
                messages.success(
                    request,
                    "Item marked as RETURN RECEIVED.",
                )

            elif action == "mark_received":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get(
                        "item_id"
                    ),
                    batch=batch,
                )

                mark_item_received(
                    item,
                    request.user,
                    received_person=(
                        request.POST.get(
                            "received_person",
                            "",
                        )
                    ),
                    confirmation_method=(
                        request.POST.get(
                            "confirmation_method",
                            "",
                        )
                    ),
                    note=request.POST.get(
                        "note",
                        "",
                    ),
                )
                messages.success(
                    request,
                    (
                        "Item marked as "
                        "RECEIVED."
                    ),
                )

            elif action == "mark_paid":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get(
                        "item_id"
                    ),
                    batch=batch,
                )

                mark_item_paid(
                    item,
                    request.user,
                    carrier_fee=(
                        request.POST.get(
                            "carrier_fee",
                            "",
                        )
                    ),
                    carrier_reference=(
                        request.POST.get(
                            "carrier_reference",
                            "",
                        )
                    ),
                    note=request.POST.get(
                        "note",
                        "",
                    ),
                )
                messages.success(
                    request,
                    "Item marked as PAID.",
                )

            elif action == "mark_returned":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get(
                        "item_id"
                    ),
                    batch=batch,
                )

                mark_item_returned(
                    item,
                    request.user,
                    return_reason=(
                        request.POST.get(
                            "return_reason",
                            "",
                        )
                    ),
                    note=request.POST.get(
                        "note",
                        "",
                    ),
                )
                messages.success(
                    request,
                    (
                        "Item marked as "
                        "RETURNED."
                    ),
                )

            elif action == "settle_seller":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get(
                        "item_id"
                    ),
                    batch=batch,
                )
                mark_item_seller_settled(
                    item,
                    request.user,
                )
                messages.success(
                    request,
                    (
                        "Seller settlement "
                        "confirmed."
                    ),
                )

            elif action == "undo_settlement":
                item = get_object_or_404(
                    ProvinceCODItem,
                    pk=request.POST.get(
                        "item_id"
                    ),
                    batch=batch,
                )
                undo_seller_settlement(item)
                messages.success(
                    request,
                    (
                        "Seller settlement "
                        "undone."
                    ),
                )

            else:
                messages.error(
                    request,
                    "Unknown action.",
                )

        except ValueError as exc:
            messages.error(
                request,
                str(exc),
            )

        return redirect(
            "provincecod:batch_detail",
            pk=batch.id,
        )

    items = list(
        batch.items
        .select_related(
            "order",
            "order__seller",
            "received_confirmed_by",
            "paid_confirmed_by",
            "returned_confirmed_by",
            "seller_settled_by",
        )
        .order_by(
            "order__seller__name",
            "id",
        )
    )

    totals = {
        "count": len(items),
        "original_cod": sum(
            (
                money(item.original_cod)
                for item in items
            ),
            ZERO,
        ),
        "province_fee": sum(
            (
                money(item.province_fee)
                for item in items
            ),
            ZERO,
        ),
        "carrier_fee": sum(
            (
                money(item.carrier_fee)
                for item in items
            ),
            ZERO,
        ),
        "net_cod": sum(
            (
                money(item.net_cod)
                for item in items
            ),
            ZERO,
        ),
        "sent": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_SENT
        ),
        "at_station": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_AT_STATION
        ),
        "out_for_delivery": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_OUT_FOR_DELIVERY
        ),
        "delivery_issue": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_DELIVERY_ISSUE
        ),
        "returning": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_RETURNING
        ),
        "return_received": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_RETURN_RECEIVED
        ),
        "received": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_RECEIVED
        ),
        "paid": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_PAID
        ),
        "returned": sum(
            1
            for item in items
            if item.cod_status
            == ProvinceCODItem.STATUS_RETURNED
        ),
        "settled": sum(
            1
            for item in items
            if item.seller_settled
        ),
    }

    for item in items:
        item.suggested_fee_display = (
            item.suggested_carrier_fee()
        )

    return render(
        request,
        "provincecod/batch_detail.html",
        {
            "batch": batch,
            "items": items,
            "totals": totals,
            "shippers": _active_carriers(),
            "confirmation_methods": (
                ProvinceCODItem
                .CONFIRMATION_METHOD_CHOICES
            ),
        },
    )


@login_required
def province_cod_report_excel(request):
    """Download the current filtered Province COD list as Excel."""
    return export_province_cod_report_xlsx(
        request
    )


@login_required
def province_cod_report(request):
    """Combined Province COD list across non-cancelled batches."""

    if request.method == "POST":
        action = (
            request.POST.get("action") or ""
        ).strip()

        if action == "update_tracking_number":
            item_id = (
                request.POST.get("item_id")
                or ""
            ).strip()

            tracking_number = (
                request.POST.get(
                    "tracking_number"
                )
                or ""
            ).strip()

            if not item_id.isdigit():
                messages.error(
                    request,
                    "Invalid Province COD item.",
                )

            elif not tracking_number:
                messages.error(
                    request,
                    (
                        "Please enter the carrier "
                        "tracking number."
                    ),
                )

            elif len(tracking_number) > 255:
                messages.error(
                    request,
                    (
                        "Tracking number cannot "
                        "exceed 255 characters."
                    ),
                )

            else:
                item = get_object_or_404(
                    ProvinceCODItem.objects
                    .exclude(
                        batch__status=(
                            ProvinceCODBatch
                            .STATUS_CANCELLED
                        )
                    ),
                    pk=int(item_id),
                )

                item.tracking_number = (
                    tracking_number
                )
                item.save()

                messages.success(
                    request,
                    (
                        "Carrier tracking saved: "
                        f"{tracking_number}"
                    ),
                )

            next_query = (
                request.POST.get(
                    "next_query"
                )
                or ""
            ).strip()

            target = reverse(
                "provincecod:report"
            )

            if next_query:
                target = (
                    f"{target}?{next_query}"
                )

            return redirect(target)

        selected_ids = [
            int(value)
            for value in request.POST.getlist(
                "selected_ids"
            )
            if str(value).isdigit()
        ]

        item_id = (
            request.POST.get("item_id")
            or ""
        ).strip()

        if (
            item_id.isdigit()
            and int(item_id)
            not in selected_ids
        ):
            selected_ids.append(
                int(item_id)
            )

        if not selected_ids:
            messages.error(
                request,
                (
                    "Please select at least "
                    "one order."
                ),
            )

        else:
            items = list(
                ProvinceCODItem.objects
                .select_related(
                    "batch",
                    "order",
                    "order__seller",
                )
                .filter(
                    pk__in=selected_ids
                )
                .exclude(
                    batch__status=(
                        ProvinceCODBatch
                        .STATUS_CANCELLED
                    )
                )
                .order_by("id")
            )

            updated = 0
            skipped = []

            for item in items:
                try:
                    if action == "mark_at_station":
                        mark_item_at_station(
                            item,
                            request.user,
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "mark_out_for_delivery":
                        mark_item_out_for_delivery(
                            item,
                            request.user,
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "mark_delivery_issue":
                        mark_item_delivery_issue(
                            item,
                            request.user,
                            issue_reason=request.POST.get(
                                "issue_reason",
                                "",
                            ),
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "mark_returning":
                        mark_item_returning(
                            item,
                            request.user,
                            return_reason=request.POST.get(
                                "return_reason",
                                "",
                            ),
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "mark_return_received":
                        mark_item_return_received(
                            item,
                            request.user,
                            received_person=request.POST.get(
                                "received_person",
                                "",
                            ),
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "mark_received":
                        mark_item_received(
                            item,
                            request.user,
                            received_person=(
                                request.POST.get(
                                    "received_person",
                                    "",
                                )
                            ),
                            confirmation_method=(
                                request.POST.get(
                                    (
                                        "confirmation_"
                                        "method"
                                    ),
                                    (
                                        ProvinceCODItem
                                        .METHOD_CARRIER
                                    ),
                                )
                            ),
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "mark_paid":
                        _mark_paid_from_report(
                            item,
                            request.user,
                            paid_amount_raw=(
                                request.POST.get(
                                    "paid_amount",
                                    "",
                                )
                            ),
                            payment_received_date=(
                                request.POST.get(
                                    (
                                        "payment_"
                                        "received_date"
                                    ),
                                    "",
                                )
                            ),
                            carrier_reference=(
                                request.POST.get(
                                    (
                                        "carrier_"
                                        "reference"
                                    ),
                                    "",
                                )
                            ),
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "mark_returned":
                        mark_item_returned(
                            item,
                            request.user,
                            return_reason=(
                                request.POST.get(
                                    "return_reason",
                                    "",
                                )
                            ),
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "record_call":
                        _record_call_note(
                            item,
                            request.user,
                            call_reason=(
                                request.POST.get(
                                    "call_reason",
                                    "",
                                )
                            ),
                            note=request.POST.get(
                                "note",
                                "",
                            ),
                        )

                    elif action == "settle_seller":
                        mark_item_seller_settled(
                            item,
                            request.user,
                        )

                    elif (
                        action
                        == "undo_settlement"
                    ):
                        undo_seller_settlement(
                            item
                        )

                    else:
                        raise ValueError(
                            (
                                "Unknown Province "
                                "COD action."
                            )
                        )

                    updated += 1

                except ValueError as exc:
                    skipped.append(
                        (
                            f"{item.order.tracking_no}: "
                            f"{exc}"
                        )
                    )

            if updated:
                messages.success(
                    request,
                    (
                        f"Updated {updated} "
                        "Province COD order(s)."
                    ),
                )

            if skipped:
                preview = "; ".join(
                    skipped[:5]
                )
                remaining = (
                    len(skipped) - 5
                )

                if remaining > 0:
                    preview += (
                        f"; and {remaining} more"
                    )

                messages.warning(
                    request,
                    f"Skipped: {preview}",
                )

        next_query = (
            request.POST.get(
                "next_query"
            )
            or ""
        ).strip()

        target = reverse(
            "provincecod:report"
        )

        if next_query:
            target = f"{target}?{next_query}"

        return redirect(target)

    date_from = (
        request.GET.get("date_from")
        or ""
    ).strip()

    date_to = (
        request.GET.get("date_to")
        or ""
    ).strip()

    status = (
        request.GET.get("status")
        or ""
    ).strip().upper()

    settlement_value = request.GET.get(
        "settlement"
    )

    if settlement_value is None:
        settlement = "UNSETTLED"
    else:
        settlement = (
            settlement_value
            .strip()
            .upper()
        )

    seller_id = (
        request.GET.get("seller")
        or ""
    ).strip()

    shipper_id = (
        request.GET.get("shipper")
        or ""
    ).strip()

    q = (
        request.GET.get("q")
        or ""
    ).strip()

    sort = (
        request.GET.get("sort")
        or "sent_date"
    ).strip().lower()

    direction = (
        request.GET.get("direction")
        or "desc"
    ).strip().lower()

    if direction not in {
        "asc",
        "desc",
    }:
        direction = "desc"

    sort_map = {
        "id": "id",
        "sent_date": "activity_date",
        "batch": "batch_id",
        "tracking": "order__tracking_no",
        "carrier_tracking": (
            "tracking_number"
        ),
        "seller": "order__seller__name",
        "carrier": (
            "batch__shipper__name"
        ),
        "receiver": (
            "order__receiver_name"
        ),
        "phone": (
            "order__receiver_phone"
        ),
        "location": (
            "order__receiver_address"
        ),
        "original_cod": "original_cod",
        "status": "cod_status",
        "net_cod": "net_cod",
        "paid_date": "paid_at",
        "reference": (
            "carrier_reference"
        ),
        "settled": "seller_settled",
        "updated": "updated_at",
    }

    if sort not in sort_map:
        sort = "sent_date"

    rows = (
        ProvinceCODItem.objects
        .select_related(
            "batch",
            "batch__shipper",
            "order",
            "order__seller",
            "received_confirmed_by",
            "paid_confirmed_by",
            "returned_confirmed_by",
            "seller_settled_by",
        )
        .exclude(
            batch__status=(
                ProvinceCODBatch
                .STATUS_CANCELLED
            )
        )
        .annotate(
            activity_date=Coalesce(
                "sent_at",
                "batch__created_at",
            ),
        )
    )

    if date_from:
        rows = rows.filter(
            activity_date__date__gte=(
                date_from
            )
        )

    if date_to:
        rows = rows.filter(
            activity_date__date__lte=(
                date_to
            )
        )

    if status == "PENDING":
        rows = rows.filter(
            cod_status=""
        )

    elif status:
        rows = rows.filter(
            cod_status=status
        )

    if settlement == "SETTLED":
        rows = rows.filter(
            seller_settled=True
        )

    elif settlement == "UNSETTLED":
        rows = rows.filter(
            seller_settled=False
        )

    if seller_id.isdigit():
        rows = rows.filter(
            order__seller_id=(
                int(seller_id)
            )
        )

    if shipper_id.isdigit():
        rows = rows.filter(
            batch__shipper_id=(
                int(shipper_id)
            )
        )

    if q:
        rows = rows.filter(
            Q(
                order__tracking_no__icontains=q
            )
            | Q(
                tracking_number__icontains=q
            )
            | Q(
                order__receiver_name__icontains=q
            )
            | Q(
                order__receiver_phone__icontains=q
            )
            | Q(
                order__receiver_address__icontains=q
            )
            | Q(
                order__seller__name__icontains=q
            )
            | Q(
                batch__shipper__name__icontains=q
            )
            | Q(
                carrier_reference__icontains=q
            )
            | Q(
                received_person__icontains=q
            )
            | Q(
                return_reason__icontains=q
            )
            | Q(note__icontains=q)
        )

    order_field = sort_map[sort]

    if direction == "desc":
        order_field = (
            f"-{order_field}"
        )

    rows = list(
        rows.order_by(
            order_field,
            "-id",
        )
    )

    for item in rows:
        item.display_status = (
            item.cod_status
            or "PENDING"
        )

    summary = {
        "count": len(rows),
        "original_cod": sum(
            (
                money(item.original_cod)
                for item in rows
            ),
            ZERO,
        ),
        "net_cod": sum(
            (
                money(item.net_cod)
                for item in rows
                if (
                    item.cod_status
                    == ProvinceCODItem
                    .STATUS_PAID
                )
            ),
            ZERO,
        ),
        "pending": sum(
            1
            for item in rows
            if not item.cod_status
        ),
        "sent": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_SENT
            )
        ),
        "at_station": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_AT_STATION
            )
        ),
        "out_for_delivery": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_OUT_FOR_DELIVERY
            )
        ),
        "delivery_issue": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_DELIVERY_ISSUE
            )
        ),
        "returning": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_RETURNING
            )
        ),
        "return_received": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_RETURN_RECEIVED
            )
        ),
        "received": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_RECEIVED
            )
        ),
        "paid": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_PAID
            )
        ),
        "returned": sum(
            1
            for item in rows
            if (
                item.cod_status
                == ProvinceCODItem
                .STATUS_RETURNED
            )
        ),
        "settled": sum(
            1
            for item in rows
            if item.seller_settled
        ),
    }

    current_params = request.GET.copy()

    if "settlement" not in current_params:
        current_params[
            "settlement"
        ] = "UNSETTLED"

    sort_urls = {}

    for key in sort_map:
        params = current_params.copy()
        next_direction = "asc"

        if (
            sort == key
            and direction == "asc"
        ):
            next_direction = "desc"

        params["sort"] = key
        params["direction"] = (
            next_direction
        )
        sort_urls[key] = (
            f"?{params.urlencode()}"
        )

    seller_ids = (
        ProvinceCODItem.objects
        .exclude(
            batch__status=(
                ProvinceCODBatch
                .STATUS_CANCELLED
            )
        )
        .exclude(
            order__seller_id__isnull=True
        )
        .values_list(
            "order__seller_id",
            flat=True,
        )
        .distinct()
    )

    sellers = (
        Seller.objects
        .filter(pk__in=seller_ids)
        .order_by("name")
    )

    return render(
        request,
        "provincecod/report.html",
        {
            "rows": rows,
            "summary": summary,
            "date_from": date_from,
            "date_to": date_to,
            "status": status,
            "settlement": settlement,
            "seller_id": seller_id,
            "shipper_id": shipper_id,
            "q": q,
            "sort": sort,
            "direction": direction,
            "sort_urls": sort_urls,
            "current_query": (
                current_params.urlencode()
            ),
            "sellers": sellers,
            "shippers": _active_carriers(),
            "today": (
                timezone.localdate()
                .isoformat()
            ),
            "call_reason_choices": (
                CALL_REASON_CHOICES
            ),
            "confirmation_methods": (
                ProvinceCODItem
                .CONFIRMATION_METHOD_CHOICES
            ),
        },
    )
