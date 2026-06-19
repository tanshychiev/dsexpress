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

        # DONE is already used by the existing province delivery flow/report.
        # update() is used so done_at is not cleared by Order.save().
        Order.objects.filter(pk=order.pk).update(
            cod=ZERO,
            delivery_fee=ZERO,
            province_fee=item.province_fee,
            additional_fee=ZERO,
            delivery_shipper=batch.shipper,
            status="DONE",
            done_at=sent_date,
            updated_at=sent_time,
            updated_by=user,
        )

        log_order_activity(
            order=order,
            user=user,
            action="COMPLETE_PROVINCE_COD_SENT",
            old_status=old_status,
            new_status="DONE",
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
def mark_item_received(
    item,
    user,
    received_person="",
    confirmation_method="",
    note="",
):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .select_related("order", "batch")
        .get(pk=item.pk)
    )

    if item.cod_status not in {
        ProvinceCODItem.STATUS_SENT,
        ProvinceCODItem.STATUS_RECEIVED,
    }:
        raise ValueError(
            "Only a sent item can be marked as received."
        )

    item.cod_status = ProvinceCODItem.STATUS_RECEIVED
    item.received_at = timezone.now()
    item.received_confirmed_by = user
    item.received_person = (received_person or "").strip()
    item.confirmation_method = (confirmation_method or "").strip()
    item.note = (note or item.note or "").strip()
    item.net_cod = ZERO

    item.save(
        update_fields=[
            "cod_status",
            "received_at",
            "received_confirmed_by",
            "received_person",
            "confirmation_method",
            "note",
            "net_cod",
            "updated_at",
        ]
    )

    return item


@transaction.atomic
def mark_item_paid(
    item,
    user,
    carrier_fee=None,
    carrier_reference="",
    note="",
):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .select_related("order", "batch")
        .get(pk=item.pk)
    )

    if item.cod_status not in {
        ProvinceCODItem.STATUS_SENT,
        ProvinceCODItem.STATUS_RECEIVED,
    }:
        raise ValueError(
            "Only a sent or received item can be marked as paid."
        )

    if carrier_fee in (None, ""):
        final_carrier_fee = item.suggested_carrier_fee()
    else:
        final_carrier_fee = money(carrier_fee)

    if final_carrier_fee < ZERO:
        raise ValueError("Carrier fee cannot be negative.")

    if final_carrier_fee > money(item.original_cod):
        raise ValueError(
            "Carrier fee cannot be higher than the COD amount."
        )

    paid_time = timezone.now()

    if not item.received_at:
        item.received_at = paid_time
        item.received_confirmed_by = user
        item.confirmation_method = ProvinceCODItem.METHOD_CARRIER

    item.cod_status = ProvinceCODItem.STATUS_PAID
    item.paid_at = paid_time
    item.paid_confirmed_by = user
    item.carrier_fee = final_carrier_fee
    item.net_cod = money(item.original_cod - final_carrier_fee)
    item.carrier_reference = (carrier_reference or "").strip()
    item.note = (note or item.note or "").strip()

    item.save(
        update_fields=[
            "cod_status",
            "received_at",
            "received_confirmed_by",
            "confirmation_method",
            "paid_at",
            "paid_confirmed_by",
            "carrier_fee",
            "net_cod",
            "carrier_reference",
            "note",
            "updated_at",
        ]
    )

    return item


@transaction.atomic
def mark_item_returned(
    item,
    user,
    return_reason="",
    note="",
):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .select_related("order", "batch")
        .get(pk=item.pk)
    )

    if item.cod_status == ProvinceCODItem.STATUS_PAID:
        raise ValueError("A paid item cannot be changed to returned.")

    if item.cod_status not in {
        ProvinceCODItem.STATUS_SENT,
        ProvinceCODItem.STATUS_RECEIVED,
        ProvinceCODItem.STATUS_RETURNED,
    }:
        raise ValueError("This item has not been sent yet.")

    item.cod_status = ProvinceCODItem.STATUS_RETURNED
    item.returned_at = timezone.now()
    item.returned_confirmed_by = user
    item.return_reason = (return_reason or "").strip()
    item.carrier_fee = ZERO
    item.net_cod = ZERO
    item.seller_settled = False
    item.seller_settled_at = None
    item.seller_settled_by = None
    item.note = (note or item.note or "").strip()

    item.save(
        update_fields=[
            "cod_status",
            "returned_at",
            "returned_confirmed_by",
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

    return item


@transaction.atomic
def mark_item_seller_settled(item, user):
    item = (
        ProvinceCODItem.objects
        .select_for_update()
        .get(pk=item.pk)
    )

    if item.cod_status != ProvinceCODItem.STATUS_PAID:
        raise ValueError("Only a paid item can be settled.")

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
    item = ProvinceCODItem.objects.select_for_update().get(pk=item.pk)

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
