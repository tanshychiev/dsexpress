from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from masterdata.models import SellerPriceRule
from orders.models import Order, OrderActivity

from .models import ProvinceCODBatch, ProvinceCODItem


ZERO = Decimal("0.00")
MONEY_STEP = Decimal("0.01")


def money(value):
    try:
        return Decimal(str(value or 0)).quantize(
            MONEY_STEP,
            rounding=ROUND_HALF_UP,
        )
    except Exception:
        return ZERO


def normalize_percent(value):
    try:
        rate = Decimal(str(value or 0))
    except Exception:
        return Decimal("0.000000")

    # Allows 0.01 = 1% and 1 = 1%.
    if rate >= 1:
        rate = rate / Decimal("100")

    return rate.quantize(Decimal("0.000001"))


def get_province_cod_rule(order, shipper):
    seller = getattr(order, "seller", None)

    if not seller or not shipper:
        return None

    rule = (
        SellerPriceRule.objects
        .filter(
            seller=seller,
            shipper=shipper,
            rule_type=SellerPriceRule.TYPE_COD,
            is_active=True,
        )
        .order_by("-id")
        .first()
    )

    if rule:
        return rule

    return (
        SellerPriceRule.objects
        .filter(
            seller=seller,
            shipper=shipper,
            rule_type=SellerPriceRule.TYPE_PV,
            is_active=True,
        )
        .order_by("-id")
        .first()
    )


def log_order_activity(
    *,
    order,
    user,
    action,
    old_status,
    new_status,
    shipper=None,
    note="",
):
    OrderActivity.objects.create(
        order=order,
        action=action,
        old_status=old_status or "",
        new_status=new_status or "",
        actor=user,
        shipper=shipper,
        note=note or "",
    )


def prepare_item_pricing(item):
    order = item.order
    shipper = item.batch.shipper
    rule = get_province_cod_rule(order, shipper)

    if not rule:
        item.province_fee = ZERO
        item.carrier_fixed_fee = ZERO
        item.carrier_percent_rate = Decimal("0.000000")
        return None

    item.province_fee = money(rule.delivery_fee)
    item.carrier_fixed_fee = money(rule.additional_fee)
    item.carrier_percent_rate = normalize_percent(rule.percent_cod)
    return rule


@transaction.atomic
def remove_pending_item(item, user):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .select_related("order", "batch")
        .get(pk=item.pk)
    )

    if item.batch.status != ProvinceCODBatch.STATUS_PENDING:
        raise ValueError("Only a pending batch can be edited.")

    order = item.order
    old_status = str(order.status or "").upper()
    restore_status = item.status_before or "INBOUND"

    Order.objects.filter(pk=order.pk).update(
        status=restore_status,
        cod=item.original_cod,
        delivery_shipper=None,
        updated_at=timezone.now(),
        updated_by=user,
    )

    log_order_activity(
        order=order,
        user=user,
        action="REMOVE_FROM_PROVINCE_COD",
        old_status=old_status,
        new_status=restore_status,
        shipper=item.batch.shipper,
        note=f"Removed from Province COD batch PVCOD-{item.batch_id}.",
    )

    item.delete()


@transaction.atomic
def cancel_pending_batch(batch, user):
    batch = (
        ProvinceCODBatch.objects
        .select_for_update()
        .get(pk=batch.pk)
    )

    if batch.status != ProvinceCODBatch.STATUS_PENDING:
        raise ValueError("Only a pending batch can be cancelled.")

    items = list(
        batch.items
        .select_for_update()
        .select_related("order")
        .order_by("id")
    )

    for item in items:
        order = item.order
        old_status = str(order.status or "").upper()
        restore_status = item.status_before or "INBOUND"

        Order.objects.filter(pk=order.pk).update(
            status=restore_status,
            cod=item.original_cod,
            delivery_shipper=None,
            updated_at=timezone.now(),
            updated_by=user,
        )

        log_order_activity(
            order=order,
            user=user,
            action="CANCEL_PROVINCE_COD",
            old_status=old_status,
            new_status=restore_status,
            shipper=batch.shipper,
            note=f"Cancelled Province COD batch PVCOD-{batch.id}.",
        )

    batch.status = ProvinceCODBatch.STATUS_CANCELLED
    batch.cancelled_at = timezone.now()
    batch.cancelled_by = user
    batch.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
        ]
    )

    return batch


@transaction.atomic
def complete_batch_sent(batch, user):
    batch = (
        ProvinceCODBatch.objects
        .select_for_update()
        .select_related("shipper")
        .get(pk=batch.pk)
    )

    if batch.status != ProvinceCODBatch.STATUS_PENDING:
        raise ValueError("Only a pending batch can be completed.")

    if not batch.shipper_id:
        raise ValueError("Please assign a carrier first.")

    items = list(
        batch.items
        .select_for_update()
        .select_related("order", "order__seller")
        .order_by("id")
    )

    if not items:
        raise ValueError("This batch has no orders.")

    sent_time = timezone.now()
    sent_date = timezone.localdate()

    for item in items:
        order = item.order
        old_status = str(order.status or "").upper()

        if money(item.original_cod) <= ZERO:
            raise ValueError(
                f"{order.tracking_no} does not have COD."
            )

        prepare_item_pricing(item)

        item.cod_status = ProvinceCODItem.STATUS_SENT
        item.sent_at = sent_time
        item.received_at = None
        item.paid_at = None
        item.returned_at = None
        item.carrier_fee = ZERO
        item.net_cod = ZERO
        item.seller_settled = False
        item.seller_settled_at = None
        item.seller_settled_by = None

        item.save(
            update_fields=[
                "province_fee",
                "carrier_fixed_fee",
                "carrier_percent_rate",
                "cod_status",
                "sent_at",
                "received_at",
                "paid_at",
                "returned_at",
                "carrier_fee",
                "net_cod",
                "seller_settled",
                "seller_settled_at",
                "seller_settled_by",
                "updated_at",
            ]
        )

        # Province COD orders now use the real Order status SENT.
        # update() keeps the selected done_at date.
        Order.objects.filter(pk=order.pk).update(
            cod=ZERO,
            delivery_fee=ZERO,
            province_fee=item.province_fee,
            additional_fee=ZERO,
            delivery_shipper=batch.shipper,
            status="SENT",
            done_at=sent_date,
            updated_at=sent_time,
            updated_by=user,
        )

        log_order_activity(
            order=order,
            user=user,
            action="COMPLETE_PROVINCE_COD_SENT",
            old_status=old_status,
            new_status="SENT",
            shipper=batch.shipper,
            note=(
                f"Province COD batch PVCOD-{batch.id} sent. "
                f"Original COD: {item.original_cod}. "
                f"Province fee: {item.province_fee}."
            ),
        )

    batch.status = ProvinceCODBatch.STATUS_SENT
    batch.sent_at = sent_time
    batch.sent_by = user

    if not batch.assigned_at:
        batch.assigned_at = sent_time

    batch.save(
        update_fields=[
            "status",
            "sent_at",
            "sent_by",
            "assigned_at",
        ]
    )

    return batch


@transaction.atomic
def _transition_item(item, allowed_from, new_status, timestamp_field, *, user=None, note="", extra_fields=None):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .select_related("order", "batch")
        .get(pk=item.pk)
    )
    if item.cod_status not in set(allowed_from):
        allowed = ", ".join(sorted(allowed_from))
        raise ValueError(f"Status {item.cod_status or 'EMPTY'} cannot become {new_status}. Allowed from: {allowed}.")
    now = timezone.now()
    item.cod_status = new_status
    setattr(item, timestamp_field, now)
    item.note = (note or item.note or "").strip()
    update_fields = ["cod_status", timestamp_field, "note", "updated_at"]
    for field, value in (extra_fields or {}).items():
        setattr(item, field, value)
        update_fields.append(field)
    item.save(update_fields=list(dict.fromkeys(update_fields)))
    return item


@transaction.atomic
def mark_item_at_station(item, user, note=""):
    return _transition_item(item, {ProvinceCODItem.STATUS_SENT}, ProvinceCODItem.STATUS_AT_STATION, "at_station_at", user=user, note=note)


@transaction.atomic
def mark_item_out_for_delivery(item, user, note=""):
    return _transition_item(item, {ProvinceCODItem.STATUS_AT_STATION, ProvinceCODItem.STATUS_DELIVERY_ISSUE}, ProvinceCODItem.STATUS_OUT_FOR_DELIVERY, "out_for_delivery_at", user=user, note=note)


@transaction.atomic
def mark_item_delivery_issue(item, user, issue_reason="", note=""):
    reason = (issue_reason or "").strip()
    if not reason:
        raise ValueError("Please enter the delivery issue reason.")
    return _transition_item(item, {ProvinceCODItem.STATUS_OUT_FOR_DELIVERY}, ProvinceCODItem.STATUS_DELIVERY_ISSUE, "delivery_issue_at", user=user, note=note, extra_fields={"return_reason": reason})


@transaction.atomic
def mark_item_received(item, user, received_person="", confirmation_method="", note=""):
    item = _transition_item(
        item,
        {ProvinceCODItem.STATUS_OUT_FOR_DELIVERY},
        ProvinceCODItem.STATUS_RECEIVED,
        "received_at",
        user=user, note=note,
        extra_fields={
            "received_confirmed_by": user,
            "received_person": (received_person or "").strip(),
            "confirmation_method": (confirmation_method or "").strip(),
            "net_cod": ZERO,
        },
    )
    return item


@transaction.atomic
def mark_item_paid(item, user, carrier_fee=None, carrier_reference="", note=""):
    item = (ProvinceCODItem.objects.select_for_update().select_related("order", "batch").get(pk=item.pk))
    if item.cod_status != ProvinceCODItem.STATUS_RECEIVED:
        raise ValueError("Only a received item can be settled from the carrier.")
    final_carrier_fee = item.suggested_carrier_fee() if carrier_fee in (None, "") else money(carrier_fee)
    if final_carrier_fee < ZERO:
        raise ValueError("Carrier fee cannot be negative.")
    if final_carrier_fee > money(item.original_cod):
        raise ValueError("Carrier fee cannot be higher than the COD amount.")
    item.cod_status = ProvinceCODItem.STATUS_PAID
    item.paid_at = timezone.now()
    item.paid_confirmed_by = user
    item.carrier_fee = final_carrier_fee
    item.net_cod = money(item.original_cod - final_carrier_fee)
    item.carrier_reference = (carrier_reference or "").strip()
    item.note = (note or item.note or "").strip()
    item.save(update_fields=["cod_status", "paid_at", "paid_confirmed_by", "carrier_fee", "net_cod", "carrier_reference", "note", "updated_at"])
    return item


@transaction.atomic
def mark_item_returning(
    item,
    user,
    return_reason="",
    note="",
):
    """
    Start the return workflow.

    This action is allowed from every active, non-paid delivery status.
    It saves RETURNING directly on the locked Province COD item and then
    updates the linked Order to RETURN_ASSIGNED.
    """
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .select_related(
            "order",
            "batch",
            "batch__shipper",
        )
        .get(pk=item.pk)
    )

    allowed_statuses = {
        ProvinceCODItem.STATUS_SENT,
        ProvinceCODItem.STATUS_AT_STATION,
        ProvinceCODItem.STATUS_OUT_FOR_DELIVERY,
        ProvinceCODItem.STATUS_DELIVERY_ISSUE,
        ProvinceCODItem.STATUS_RECEIVED,
    }

    if item.cod_status not in allowed_statuses:
        raise ValueError(
            "This item cannot be marked as returning from "
            f"{item.cod_status or 'EMPTY'}."
        )

    reason = (
        return_reason
        or note
        or item.return_reason
        or "Return requested"
    ).strip()

    old_order_status = str(
        item.order.status or ""
    ).upper()

    now = timezone.now()

    item.cod_status = ProvinceCODItem.STATUS_RETURNING
    item.returning_at = now
    item.return_reason = reason
    item.carrier_fee = ZERO
    item.net_cod = ZERO
    item.seller_settled = False
    item.seller_settled_at = None
    item.seller_settled_by = None

    if note:
        item.note = note.strip()

    item.save(
        update_fields=[
            "cod_status",
            "returning_at",
            "return_reason",
            "carrier_fee",
            "net_cod",
            "seller_settled",
            "seller_settled_at",
            "seller_settled_by",
            "note",
            "updated_at",
        ]
    )

    Order.objects.filter(
        pk=item.order_id
    ).update(
        status="RETURN_ASSIGNED",
        updated_at=now,
        updated_by=user,
    )

    log_order_activity(
        order=item.order,
        user=user,
        action="PROVINCE_COD_RETURNING",
        old_status=old_order_status,
        new_status="RETURN_ASSIGNED",
        shipper=item.batch.shipper,
        note=note or reason,
    )

    return item


@transaction.atomic
def mark_item_return_received(item, user, received_person="", note=""):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .select_related("order", "batch", "batch__shipper")
        .get(pk=item.pk)
    )

    old_order_status = str(item.order.status or "").upper()

    item = _transition_item(
        item,
        {ProvinceCODItem.STATUS_RETURNING},
        ProvinceCODItem.STATUS_RETURN_RECEIVED,
        "return_received_at",
        user=user,
        note=note,
        extra_fields={
            "returned_at": timezone.now(),
            "returned_confirmed_by": user,
            "received_person": (received_person or "").strip(),
            "carrier_fee": ZERO,
            "net_cod": ZERO,
            "seller_settled": False,
            "seller_settled_at": None,
            "seller_settled_by": None,
        },
    )

    Order.objects.filter(pk=item.order_id).update(
        status="RETURNED",
        updated_at=timezone.now(),
        updated_by=user,
    )

    log_order_activity(
        order=item.order,
        user=user,
        action="PROVINCE_COD_RETURN_RECEIVED",
        old_status=old_order_status,
        new_status="RETURNED",
        shipper=item.batch.shipper,
        note=note or "Returned parcel received.",
    )

    return item


@transaction.atomic
def mark_item_returned(item, user, return_reason="", note=""):
    """
    Compatibility for old buttons or old views.

    The legacy action now starts the return workflow only. It does not mark
    the parcel as received back. Staff must confirm Return Received separately.
    """
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .get(pk=item.pk)
    )

    if item.cod_status in {
        ProvinceCODItem.STATUS_SENT,
        ProvinceCODItem.STATUS_AT_STATION,
        ProvinceCODItem.STATUS_OUT_FOR_DELIVERY,
        ProvinceCODItem.STATUS_DELIVERY_ISSUE,
        ProvinceCODItem.STATUS_RECEIVED,
    }:
        return mark_item_returning(
            item,
            user,
            return_reason=return_reason,
            note=note,
        )

    if item.cod_status in {
        ProvinceCODItem.STATUS_RETURNING,
        ProvinceCODItem.STATUS_RETURN_RECEIVED,
        ProvinceCODItem.STATUS_RETURNED,
    }:
        return item

    raise ValueError(
        "This item cannot enter the return workflow from "
        f"{item.cod_status or 'EMPTY'}."
    )


@transaction.atomic
def mark_item_seller_settled(item, user):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .get(pk=item.pk)
    )

    if item.cod_status != ProvinceCODItem.STATUS_PAID:
        raise ValueError("Only an item settled from the carrier can be settled to the customer.")

    if item.seller_settled:
        return item

    item.seller_settled = True
    item.seller_settled_at = timezone.now()
    item.seller_settled_by = user
    item.save(
        update_fields=[
            "seller_settled",
            "seller_settled_at",
            "seller_settled_by",
            "updated_at",
        ]
    )
    return item


@transaction.atomic
def undo_seller_settlement(item):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .get(pk=item.pk)
    )

    item.seller_settled = False
    item.seller_settled_at = None
    item.seller_settled_by = None
    item.save(
        update_fields=[
            "seller_settled",
            "seller_settled_at",
            "seller_settled_by",
            "updated_at",
        ]
    )
    return item
