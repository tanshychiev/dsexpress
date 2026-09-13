from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from orders.models import Order

from .models import PickupBooking, PickupBookingAudit


BLOCKED_STATUSES = {
    Order.STATUS_DELIVERED,
    Order.STATUS_OUT_FOR_DELIVERY,
    Order.STATUS_PROVINCE_ASSIGNED,
    Order.STATUS_RETURNED,
    Order.STATUS_RETURNING,
    Order.STATUS_RETURN_ASSIGNED,
    Order.STATUS_VOID,
}


def _money(raw, field_name):
    value = (raw or "0").strip().replace(",", "")
    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_name} must be a valid number.")
    if amount < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return amount


def _booking_values(booking):
    return {
        "method": booking.method,
        "method_display": booking.get_method_display(),
        "cod": str(booking.cod),
        "delivery_fee": str(booking.delivery_fee),
        "additional_fee": str(booking.additional_fee),
    }


def _sync_order(order, booking, user):
    order.cod = booking.cod
    order.delivery_fee = booking.delivery_fee
    order.additional_fee = booking.additional_fee
    order.delivery_shipper = None
    order.status = Order.STATUS_DELIVERED
    order.clear_delivery = True
    order.done_at = timezone.localdate()
    order.updated_at = timezone.now()
    order.updated_by = user
    order.save()


@login_required
def pickup_booking(request):
    tracking = (request.GET.get("tracking") or "").strip()
    order = None
    existing = None

    if tracking:
        order = (
            Order.objects
            .select_related("seller", "delivery_shipper")
            .filter(tracking_no__iexact=tracking, is_deleted=False)
            .first()
        )
        if not order:
            messages.error(request, f"Tracking {tracking} was not found.")
        else:
            existing = PickupBooking.objects.filter(order=order).first()

    if request.method == "POST":
        tracking = (request.POST.get("tracking_no") or "").strip()
        method = (request.POST.get("method") or "").strip()

        if method not in {PickupBooking.METHOD_CUSTOMER, PickupBooking.METHOD_GRAB}:
            messages.error(request, "Please choose Customer Pickup or Grab / External Rider.")
            return redirect(f"/pickup-booking/?tracking={tracking}")

        try:
            cod = _money(request.POST.get("cod"), "COD")
            delivery_fee = _money(request.POST.get("delivery_fee"), "Delivery Fee")
            additional_fee = _money(request.POST.get("additional_fee"), "Additional Fee")
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(f"/pickup-booking/?tracking={tracking}")

        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update().select_related("seller"),
                tracking_no__iexact=tracking,
                is_deleted=False,
            )

            if order.status in BLOCKED_STATUSES:
                messages.error(request, f"This order cannot be completed from Pickup / Booking because its status is {order.status}. Remove it from any delivery/province batch first if you want to switch it to Pickup / Booking.")
                return redirect(f"/pickup-booking/?tracking={tracking}")

            if PickupBooking.objects.filter(order=order).exists():
                messages.info(request, "This order is already completed in Pickup / Booking. Use History to adjust the amounts.")
                return redirect("pickupbooking:history")

            booking = PickupBooking.objects.create(
                order=order,
                method=method,
                cod=cod,
                delivery_fee=delivery_fee,
                additional_fee=additional_fee,
                completed_by=request.user,
                updated_by=request.user,
            )
            _sync_order(order, booking, request.user)
            PickupBookingAudit.objects.create(
                booking=booking,
                action=PickupBookingAudit.ACTION_COMPLETED,
                old_values={},
                new_values=_booking_values(booking),
                changed_by=request.user,
            )

        messages.success(request, f"{order.tracking_no} completed as {booking.get_method_display()}.")
        return redirect("pickupbooking:home")

    return render(
        request,
        "pickupbooking/home.html",
        {
            "tracking": tracking,
            "order": order,
            "existing": existing,
            "method_choices": PickupBooking.METHOD_CHOICES,
        },
    )


@login_required
def pickup_history(request):
    q = (request.GET.get("q") or "").strip()
    method = (request.GET.get("method") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    rows = PickupBooking.objects.select_related("order", "order__seller", "completed_by", "updated_by")

    if q:
        rows = rows.filter(
            Q(order__tracking_no__icontains=q)
            | Q(order__seller__name__icontains=q)
            | Q(order__seller_order_code__icontains=q)
            | Q(order__receiver_name__icontains=q)
            | Q(order__receiver_phone__icontains=q)
        )
    if method in {PickupBooking.METHOD_CUSTOMER, PickupBooking.METHOD_GRAB}:
        rows = rows.filter(method=method)
    if date_from:
        rows = rows.filter(completed_at__date__gte=date_from)
    if date_to:
        rows = rows.filter(completed_at__date__lte=date_to)

    return render(
        request,
        "pickupbooking/history.html",
        {
            "rows": rows[:500],
            "q": q,
            "selected_method": method,
            "date_from": date_from,
            "date_to": date_to,
            "method_choices": PickupBooking.METHOD_CHOICES,
        },
    )


@login_required
def pickup_edit(request, pk):
    booking = get_object_or_404(
        PickupBooking.objects.select_related("order", "order__seller", "completed_by", "updated_by"),
        pk=pk,
    )

    if request.method == "POST":
        method = (request.POST.get("method") or "").strip()
        if method not in {PickupBooking.METHOD_CUSTOMER, PickupBooking.METHOD_GRAB}:
            messages.error(request, "Please choose a valid pickup method.")
            return redirect("pickupbooking:edit", pk=booking.pk)

        try:
            cod = _money(request.POST.get("cod"), "COD")
            delivery_fee = _money(request.POST.get("delivery_fee"), "Delivery Fee")
            additional_fee = _money(request.POST.get("additional_fee"), "Additional Fee")
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("pickupbooking:edit", pk=booking.pk)

        with transaction.atomic():
            booking = PickupBooking.objects.select_for_update().select_related("order").get(pk=booking.pk)
            order = Order.objects.select_for_update().get(pk=booking.order_id)
            old_values = _booking_values(booking)

            booking.method = method
            booking.cod = cod
            booking.delivery_fee = delivery_fee
            booking.additional_fee = additional_fee
            booking.updated_by = request.user
            booking.save()
            _sync_order(order, booking, request.user)

            new_values = _booking_values(booking)
            if old_values != new_values:
                PickupBookingAudit.objects.create(
                    booking=booking,
                    action=PickupBookingAudit.ACTION_UPDATED,
                    old_values=old_values,
                    new_values=new_values,
                    changed_by=request.user,
                )

        messages.success(request, f"{booking.order.tracking_no} updated. Delivery Report and COD now use the new amounts.")
        return redirect("pickupbooking:history")

    return render(
        request,
        "pickupbooking/edit.html",
        {
            "booking": booking,
            "method_choices": PickupBooking.METHOD_CHOICES,
            "audit_logs": booking.audit_logs.select_related("changed_by")[:30],
        },
    )
