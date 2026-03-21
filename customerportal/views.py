import json
import requests

from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import models
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from deliverpp.models import Order
from .forms import PublicBookingForm
from .models import SellerBooking, SellerPortalSession


# =========================================================
# PUBLIC PAGES
# =========================================================

def home(request):
    return render(request, "customerportal/home.html")


def _map_portal_status(order):
    if getattr(order, "clear_delivery", False):
        return "RETURN"

    status = (getattr(order, "status", "") or "").strip().upper()

    if status == "CREATED":
        return "CREATE"

    if status in ["OUT_FOR_DELIVERY", "PROVINCE_ASSIGNED", "RETURN_ASSIGNED"]:
        return "PROCESSING"

    if status == "DELIVERED":
        return "DELIVERED"

    if status in ["VOID", "RETURN", "RETURNED"]:
        return "RETURN"

    return "PROCESSING"


def _safe_pct(part, total):
    if not total:
        return 0
    try:
        return round((part * 100.0) / total, 2)
    except Exception:
        return 0


def tracking(request):
    seller = get_user_seller(request.user)

    q = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "ALL").strip().upper()
    date_from = (request.GET.get("from") or "").strip()
    date_to = (request.GET.get("to") or "").strip()
    has_searched = request.GET.get("search") == "1"

    qs = Order.objects.none()

    if seller:
        qs = Order.objects.filter(seller=seller).order_by("-id")

    if has_searched and seller:
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        if q:
            qs = qs.filter(
                models.Q(tracking_no__icontains=q)
                | models.Q(receiver_name__icontains=q)
                | models.Q(receiver_phone__icontains=q)
            )

    orders = list(qs) if has_searched and seller else []

    total_create = 0
    total_processing = 0
    total_delivered = 0
    total_return = 0

    for order in orders:
        order.main_status = _map_portal_status(order)

        if order.main_status == "CREATE":
            total_create += 1
        elif order.main_status == "PROCESSING":
            total_processing += 1
        elif order.main_status == "DELIVERED":
            total_delivered += 1
        elif order.main_status == "RETURN":
            total_return += 1

    if status_filter and status_filter != "ALL":
        orders = [o for o in orders if o.main_status == status_filter]

    total_sent = total_create + total_processing + total_delivered + total_return

    context = {
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "status_filter": status_filter,
        "has_searched": has_searched,
        "orders": orders,
        "total_sent": total_sent,
        "total_done": total_delivered,
        "total_processing": total_processing + total_create,
        "total_return": total_return,
        "total_done_pct": _safe_pct(total_delivered, total_sent),
        "total_processing_pct": _safe_pct(total_processing + total_create, total_sent),
        "total_return_pct": _safe_pct(total_return, total_sent),
    }

    return render(request, "customerportal/tracking.html", context)


# =========================================================
# HELPERS
# =========================================================

def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def get_user_seller(user):
    if not user or not user.is_authenticated:
        return None

    if user.is_staff:
        return None

    account = getattr(user, "account", None)
    if not account:
        return None

    if account.account_type != "seller":
        return None

    seller = account.seller
    if not seller:
        return None

    if not seller.is_active:
        return None

    return seller


# =========================================================
# TELEGRAM HELPERS
# =========================================================

def get_telegram_bot_token():
    return getattr(settings, "TELEGRAM_BOT_TOKEN", "")


def get_telegram_chat_id():
    return getattr(settings, "TELEGRAM_BOOKING_CHAT_ID", "")


def telegram_send_message(text, reply_markup=None):
    bot_token = get_telegram_bot_token()
    chat_id = get_telegram_chat_id()

    if not bot_token or not chat_id:
        print("Telegram bot token or chat id missing")
        return None

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        response = requests.post(url, data=payload, timeout=10)
        data = response.json()
        print("TELEGRAM SEND:", data)
        if data.get("ok"):
            return data.get("result")
    except Exception as e:
        print("Telegram send error:", e)

    return None


def telegram_edit_message_remove_buttons(chat_id, message_id, new_text):
    bot_token = get_telegram_bot_token()

    if not bot_token or not chat_id or not message_id:
        return

    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"

    try:
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": new_text,
            },
            timeout=10,
        )
        print("TELEGRAM EDIT:", response.text)
    except Exception as e:
        print("Telegram edit message error:", e)


# =========================================================
# BOOKING STATUS HELPERS
# =========================================================

def get_accept_status():
    if hasattr(SellerBooking, "STATUS_APPROVED"):
        return SellerBooking.STATUS_APPROVED
    if hasattr(SellerBooking, "STATUS_ACCEPTED"):
        return SellerBooking.STATUS_ACCEPTED
    return "APPROVED"


def get_cancel_status():
    if hasattr(SellerBooking, "STATUS_CANCELLED"):
        return SellerBooking.STATUS_CANCELLED
    if hasattr(SellerBooking, "STATUS_CANCELED"):
        return SellerBooking.STATUS_CANCELED
    if hasattr(SellerBooking, "STATUS_REJECTED"):
        return SellerBooking.STATUS_REJECTED
    return "CANCELLED"


# =========================================================
# PUBLIC BOOKING TELEGRAM
# =========================================================

def send_public_booking_to_telegram(name, phone, pickup_location, total_pc, latitude="", longitude=""):
    map_link = ""
    if latitude and longitude:
        map_link = f"\n🗺 Map: https://maps.google.com/?q={latitude},{longitude}"

    message = (
        "📦 New Booking from Website\n\n"
        f"👤 Name: {name}\n"
        f"📞 Phone: {phone}\n"
        f"📍 Address: {pickup_location}\n"
        f"📦 Total PC: {total_pc}"
        f"{map_link}"
    )

    return telegram_send_message(message)


# =========================================================
# SELLER BOOKING TELEGRAM
# =========================================================

def build_seller_booking_message(booking):
    return (
        "📦 New Seller Booking\n\n"
        f"Seller: {booking.seller.name}\n"
        f"Phone: {booking.sender_phone}\n"
        f"Address: {booking.sender_address}\n"
        f"Package: {booking.total_pc}\n"
        f"Pickup: {booking.pickup_date} {booking.pickup_time}\n"
        f"Arrive: {booking.arrive_date} {booking.arrive_time}\n"
        f"Remark: {booking.remark or '-'}\n"
        f"Status: {booking.get_status_display()}\n"
    )


def send_seller_booking_to_telegram(booking):
    text = build_seller_booking_message(booking)

    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Accept", "callback_data": f"accept:{booking.id}"},
            {"text": "❌ Cancel", "callback_data": f"cancel:{booking.id}"},
        ]]
    }

    return telegram_send_message(text, reply_markup=reply_markup)


# =========================================================
# PUBLIC BOOKING PAGE
# =========================================================

def booking_public(request):
    success_popup = False

    if request.method == "POST":
        form = PublicBookingForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data["name"]
            phone = form.cleaned_data["phone"]
            pickup_location = form.cleaned_data["pickup_location"]
            total_pc = form.cleaned_data["total_pc"]

            latitude = request.POST.get("latitude", "")
            longitude = request.POST.get("longitude", "")

            send_public_booking_to_telegram(
                name=name,
                phone=phone,
                pickup_location=pickup_location,
                total_pc=total_pc,
                latitude=latitude,
                longitude=longitude,
            )

            form = PublicBookingForm()
            success_popup = True
    else:
        form = PublicBookingForm()

    return render(
        request,
        "customerportal/booking_public.html",
        {
            "form": form,
            "success_popup": success_popup,
        },
    )


# =========================================================
# SELLER LOGIN
# =========================================================

class SellerLoginForm(forms.Form):
    username = forms.CharField()
    password = forms.CharField(widget=forms.PasswordInput)


def seller_login(request):
    if request.user.is_authenticated:
        seller = get_user_seller(request.user)
        if seller:
            return redirect("portal:dashboard")
        logout(request)

    form = SellerLoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]

        user = authenticate(request, username=username, password=password)

        if user is None:
            form.add_error(None, "Invalid username or password.")
        elif user.is_staff:
            form.add_error(None, "Staff account cannot login to seller portal.")
        else:
            account = getattr(user, "account", None)

            if not account:
                form.add_error(None, "This account is not a seller portal account.")
            elif account.account_type != "seller":
                form.add_error(None, "This account is not a seller portal account.")
            elif not account.seller:
                form.add_error(None, "This account is not a seller portal account.")
            elif not account.seller.is_active:
                form.add_error(None, "This seller account is inactive.")
            else:
                login(request, user)

                SellerPortalSession.objects.create(
                    seller=account.seller,
                    user=user,
                    login_at=timezone.now(),
                    last_activity_at=timezone.now(),
                    ip_address=get_client_ip(request),
                    user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
                )

                return redirect("portal:dashboard")

    return render(request, "customerportal/login.html", {"form": form})


def seller_logout(request):
    seller = get_user_seller(request.user)

    if seller:
        session = (
            SellerPortalSession.objects
            .filter(
                user=request.user,
                seller=seller,
                logout_at__isnull=True,
            )
            .order_by("-login_at")
            .first()
        )
        if session:
            session.logout_at = timezone.now()
            session.last_activity_at = timezone.now()
            session.save(update_fields=["logout_at", "last_activity_at"])

    logout(request)
    return redirect("portal:login")


# =========================================================
# SELLER HELPER
# =========================================================

def _get_logged_in_seller(request):
    return get_user_seller(request.user)


# =========================================================
# SELLER BOOKING PAGE
# =========================================================

@login_required
def booking_seller(request):
    seller = _get_logged_in_seller(request)
    if seller is None:
        logout(request)
        return redirect("portal:login")

    success_popup = False
    error_message = ""

    if request.method == "POST":
        sender_phone = (request.POST.get("sender_phone") or "").strip()
        sender_address = (request.POST.get("sender_address") or "").strip()
        total_pc_raw = (request.POST.get("total_pc") or "1").strip()
        remark = (request.POST.get("remark") or "").strip()

        pickup_date_raw = (request.POST.get("pickup_date") or "").strip()
        pickup_time = (request.POST.get("pickup_time") or "").strip()
        arrive_date_raw = (request.POST.get("arrive_date") or "").strip()
        arrive_time = (request.POST.get("arrive_time") or "").strip()

        try:
            total_pc = max(int(total_pc_raw), 1)
        except Exception:
            total_pc = 1

        if not sender_phone:
            error_message = "Please enter phone number."
        elif not sender_address:
            error_message = "Please enter pickup address."
        elif not pickup_date_raw or not pickup_time or not arrive_date_raw or not arrive_time:
            error_message = "Please check estimate date and time."
        else:
            booking = SellerBooking.objects.create(
                seller=seller,
                sender_phone=sender_phone,
                sender_address=sender_address,
                total_pc=total_pc,
                remark=remark,
                pickup_date=pickup_date_raw,
                pickup_time=pickup_time,
                arrive_date=arrive_date_raw,
                arrive_time=arrive_time,
                status=SellerBooking.STATUS_PENDING,
            )

            telegram_result = send_seller_booking_to_telegram(booking)

            if telegram_result:
                booking.telegram_chat_id = str(telegram_result.get("chat", {}).get("id", ""))
                booking.telegram_message_id = str(telegram_result.get("message_id", ""))
                booking.save(update_fields=["telegram_chat_id", "telegram_message_id"])

            success_popup = True

    return render(
        request,
        "customerportal/booking_seller.html",
        {
            "seller": seller,
            "success_popup": success_popup,
            "error_message": error_message,
        },
    )


# =========================================================
# BOOKING HISTORY
# =========================================================

@login_required
def booking_history(request):
    seller = _get_logged_in_seller(request)
    if seller is None:
        logout(request)
        return redirect("portal:login")

    bookings = SellerBooking.objects.filter(seller=seller).order_by("-created_at")

    return render(
        request,
        "customerportal/booking_history.html",
        {
            "seller": seller,
            "bookings": bookings,
        },
    )


# =========================================================
# DASHBOARD
# =========================================================

@login_required
def dashboard(request):
    seller = _get_logged_in_seller(request)
    if seller is None:
        logout(request)
        return redirect("portal:login")

    qs = Order.objects.filter(seller=seller)

    context = {
        "seller": seller,
        "total_parcels": qs.count(),
        "pending_parcels": qs.filter(status="CREATED").count(),
        "out_for_delivery": qs.filter(status="OUT_FOR_DELIVERY").count(),
        "delivered_parcels": qs.filter(status="DELIVERED").count(),
        "cod_balance": qs.filter(status="DELIVERED").aggregate(total=Sum("price"))["total"] or 0,
        "recent_orders": qs.order_by("-id")[:10],
    }

    return render(request, "customerportal/dashboard.html", context)


# =========================================================
# ORDERS
# =========================================================

@login_required
def orders(request):
    seller = _get_logged_in_seller(request)
    if seller is None:
        logout(request)
        return redirect("portal:login")

    qs = Order.objects.filter(seller=seller).order_by("-id")

    status = request.GET.get("status", "").strip()
    search = request.GET.get("q", "").strip()

    if status:
        qs = qs.filter(status=status)

    if search:
        qs = qs.filter(tracking_no__icontains=search)

    return render(
        request,
        "customerportal/orders.html",
        {
            "seller": seller,
            "orders": qs,
            "selected_status": status,
            "search_query": search,
        },
    )


# =========================================================
# COD REPORT
# =========================================================

@login_required
def cod_report(request):
    seller = _get_logged_in_seller(request)
    if seller is None:
        logout(request)
        return redirect("portal:login")

    qs = Order.objects.filter(seller=seller).order_by("-id")
    delivered_qs = qs.filter(status="DELIVERED")
    total_cod = delivered_qs.aggregate(total=Sum("price"))["total"] or 0

    context = {
        "seller": seller,
        "orders": delivered_qs,
        "total_cod": total_cod,
        "delivered_count": delivered_qs.count(),
    }

    return render(request, "customerportal/cod_report.html", context)


# =========================================================
# CHANGE PASSWORD
# =========================================================

@login_required
def change_password(request):
    seller = _get_logged_in_seller(request)
    if seller is None:
        logout(request)
        return redirect("portal:login")

    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            return redirect("portal:dashboard")
    else:
        form = PasswordChangeForm(request.user)

    return render(
        request,
        "customerportal/change_password.html",
        {
            "seller": seller,
            "form": form,
        },
    )


# =========================================================
# API FOR TELEGRAM POLLING BOT
# =========================================================

@csrf_exempt
def telegram_update_booking(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST only"}, status=405)

    try:
        data = json.loads(request.body)
        print("TELEGRAM UPDATE DATA:", data)

        booking_id = data.get("booking_id")
        action = data.get("action")
        telegram_user_id = data.get("telegram_user_id")
        telegram_name = data.get("telegram_name")
        telegram_username = data.get("telegram_username")

        print("telegram_user_id =", telegram_user_id)
        print("telegram_name =", telegram_name)
        print("telegram_username =", telegram_username)

        booking = SellerBooking.objects.get(id=booking_id)

        if booking.status != SellerBooking.STATUS_PENDING:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Booking already processed",
                },
                status=400,
            )

        if action == "accept":
            booking.status = get_accept_status()
            action_text = "✅ Accepted"
        elif action == "cancel":
            booking.status = get_cancel_status()
            action_text = "❌ Cancelled"
        else:
            return JsonResponse({"success": False, "error": "Invalid action"}, status=400)

        display_name = ""
        if telegram_name and str(telegram_name).strip():
            display_name = str(telegram_name).strip()
        elif telegram_username and str(telegram_username).strip():
            display_name = str(telegram_username).strip()
        elif telegram_user_id:
            display_name = f"Telegram {telegram_user_id}"
        else:
            display_name = "Unknown"

        booking.processed_by_telegram_name = display_name
        booking.processed_by_telegram_id = str(telegram_user_id or "")
        booking.processed_at = timezone.now()
        booking.save()

        print("SAVED processed_by_telegram_name =", booking.processed_by_telegram_name)
        print("SAVED processed_by_telegram_id =", booking.processed_by_telegram_id)

        new_text = (
            "📦 New Seller Booking\n\n"
            f"Seller: {booking.seller.name}\n"
            f"Phone: {booking.sender_phone}\n"
            f"Address: {booking.sender_address}\n"
            f"Package: {booking.total_pc}\n"
            f"Pickup: {booking.pickup_date} {booking.pickup_time}\n"
            f"Arrive: {booking.arrive_date} {booking.arrive_time}\n"
            f"Remark: {booking.remark or '-'}\n"
            f"Status: {booking.get_status_display()}\n"
            f"Processed By: {display_name}\n\n"
            f"{action_text}"
        )

        if booking.telegram_chat_id and booking.telegram_message_id:
            telegram_edit_message_remove_buttons(
                booking.telegram_chat_id,
                booking.telegram_message_id,
                new_text,
            )

        return JsonResponse(
            {
                "success": True,
                "booking_id": booking.id,
                "status": booking.status,
                "processed_by": display_name,
            }
        )

    except SellerBooking.DoesNotExist:
        return JsonResponse({"success": False, "error": "Booking not found"}, status=404)
    except Exception as e:
        print("telegram_update_booking error:", e)
        return JsonResponse({"success": False, "error": str(e)}, status=500)