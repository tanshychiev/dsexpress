import json
import calendar
import os
import requests

from datetime import date, timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import models
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from orders.models import Order
from .forms import PublicBookingForm
from .models import SellerBooking, SellerPortalSession

from inventory.models import InventorySellerSetting
from inventory.services import get_seller_current_stock, get_seller_inventory_setting


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


def _parse_tracking_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except Exception:
        return None


def tracking(request):
    seller = get_user_seller(request.user)

    q = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "ALL").strip().upper()
    raw_date_from = (request.GET.get("from") or "").strip()
    raw_date_to = (request.GET.get("to") or "").strip()
    has_searched = request.GET.get("search") == "1"

    today = timezone.localdate()
    tracking_max_date = today
    tracking_min_date = today - timedelta(days=59)
    tracking_limit_days = 60

    qs = Order.objects.none()

    date_from_obj = _parse_tracking_date(raw_date_from)
    date_to_obj = _parse_tracking_date(raw_date_to)

    if has_searched:
        if date_from_obj is None:
            date_from_obj = tracking_min_date

        if date_to_obj is None:
            date_to_obj = tracking_max_date

        if date_from_obj < tracking_min_date:
            date_from_obj = tracking_min_date

        if date_to_obj > tracking_max_date:
            date_to_obj = tracking_max_date
    else:
        date_from_obj = date_from_obj or tracking_min_date
        date_to_obj = date_to_obj or tracking_max_date

    date_from = date_from_obj.isoformat() if date_from_obj else ""
    date_to = date_to_obj.isoformat() if date_to_obj else ""

    if seller:
        qs = Order.objects.filter(seller=seller).order_by("-id")

    if has_searched and seller:
        if date_from_obj and date_to_obj and date_from_obj > date_to_obj:
            qs = Order.objects.none()
        else:
            qs = qs.filter(
                created_at__date__gte=date_from_obj,
                created_at__date__lte=date_to_obj,
            )

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
        "tracking_min_date": tracking_min_date.isoformat(),
        "tracking_max_date": tracking_max_date.isoformat(),
        "tracking_limit_days": tracking_limit_days,
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


def _get_logged_in_seller(request):
    return get_user_seller(request.user)


# =========================================================
# SELLER PROFILE PHOTO HELPERS
# =========================================================

PROFILE_IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp"]
PROFILE_IMAGE_MAX_SIZE = 3 * 1024 * 1024


def _seller_profile_photo_path(seller, ext):
    return f"seller_portal/profile/seller_{seller.id}{ext.lower()}"


def _get_seller_profile_photo_url(seller):
    if not seller:
        return ""

    for ext in PROFILE_IMAGE_EXTS:
        path = _seller_profile_photo_path(seller, ext)

        try:
            if default_storage.exists(path):
                return default_storage.url(path)
        except Exception:
            pass

    return ""


def _save_seller_profile_photo(seller, upload_file):
    if not seller or not upload_file:
        return ""

    ext = os.path.splitext(upload_file.name or "")[1].lower()

    if ext not in PROFILE_IMAGE_EXTS:
        raise forms.ValidationError("Please upload JPG, PNG, or WEBP image only.")

    if upload_file.size > PROFILE_IMAGE_MAX_SIZE:
        raise forms.ValidationError("Profile photo must be under 3MB.")

    for old_ext in PROFILE_IMAGE_EXTS:
        old_path = _seller_profile_photo_path(seller, old_ext)

        try:
            if default_storage.exists(old_path):
                default_storage.delete(old_path)
        except Exception:
            pass

    new_path = _seller_profile_photo_path(seller, ext)
    saved_path = default_storage.save(new_path, ContentFile(upload_file.read()))
    return default_storage.url(saved_path)


# =========================================================
# TELEGRAM HELPERS
# =========================================================

def get_telegram_bot_token():
    return getattr(settings, "TELEGRAM_BOOKING_BOT_TOKEN", "")


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


def telegram_send_photo(photo_file, caption=""):
    bot_token = get_telegram_bot_token()
    chat_id = get_telegram_chat_id()

    if not bot_token or not chat_id:
        print("Telegram bot token or chat id missing")
        return None

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"

    try:
        photo_file.seek(0)
    except Exception:
        pass

    try:
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "caption": caption,
            },
            files={
                "photo": photo_file,
            },
            timeout=20,
        )
        data = response.json()
        print("TELEGRAM SEND PHOTO:", data)

        if data.get("ok"):
            return data.get("result")
    except Exception as e:
        print("Telegram send photo error:", e)

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


def telegram_answer_callback(callback_query_id, text=""):
    bot_token = get_telegram_bot_token()

    if not bot_token or not callback_query_id:
        return

    url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"

    try:
        response = requests.post(
            url,
            data={
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": False,
            },
            timeout=10,
        )
        print("TELEGRAM ANSWER CALLBACK:", response.text)
    except Exception as e:
        print("Telegram answer callback error:", e)


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

def send_public_booking_to_telegram(
    name,
    phone,
    pickup_location,
    total_pc,
    latitude="",
    longitude="",
    photo_file=None,
):
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

    if photo_file:
        return telegram_send_photo(photo_file, caption=message)

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
        form = PublicBookingForm(request.POST, request.FILES)

        if form.is_valid():
            name = form.cleaned_data["name"]
            phone = form.cleaned_data["phone"]
            pickup_location = form.cleaned_data["pickup_location"]
            total_pc = form.cleaned_data["total_pc"]

            latitude = request.POST.get("latitude", "")
            longitude = request.POST.get("longitude", "")
            photo_file = request.FILES.get("photo")

            send_public_booking_to_telegram(
                name=name,
                phone=phone,
                pickup_location=pickup_location,
                total_pc=total_pc,
                latitude=latitude,
                longitude=longitude,
                photo_file=photo_file,
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


def _seller_login_page(request, template_name, success_url):
    """
    Shared seller authentication for mobile and computer portal login pages.
    Staff users are never allowed into the seller portal.
    """
    if request.user.is_authenticated:
        seller = get_user_seller(request.user)

        if seller:
            return redirect(success_url)

        logout(request)

    form = SellerLoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]

        user = authenticate(
            request,
            username=username,
            password=password,
        )

        if user is None:
            form.add_error(None, "Invalid username or password.")

        elif user.is_staff:
            form.add_error(
                None,
                "Staff account cannot login to seller portal.",
            )

        else:
            account = getattr(user, "account", None)
            seller = getattr(account, "seller", None) if account else None
            account_type = getattr(account, "account_type", "") if account else ""

            if not account or account_type != "seller":
                form.add_error(
                    None,
                    "This account is not a seller portal account.",
                )

            elif not seller:
                form.add_error(
                    None,
                    "This account is not connected to a seller.",
                )

            elif not seller.is_active:
                form.add_error(
                    None,
                    "This seller account is inactive.",
                )

            else:
                login(request, user)

                remember_me = request.POST.get("remember_me") == "on"

                if remember_me:
                    request.session.set_expiry(
                        settings.SESSION_COOKIE_AGE
                    )
                else:
                    request.session.set_expiry(0)

                SellerPortalSession.objects.create(
                    seller=seller,
                    user=user,
                    login_at=timezone.now(),
                    last_activity_at=timezone.now(),
                    ip_address=get_client_ip(request),
                    user_agent=request.META.get(
                        "HTTP_USER_AGENT",
                        "",
                    )[:1000],
                )

                return redirect(success_url)

    return render(
        request,
        template_name,
        {
            "form": form,
        },
    )


def seller_login(request):
    return _seller_login_page(
        request=request,
        template_name="customerportal/login.html",
        success_url="portal:dashboard",
    )


def computer_login(request):
    return _seller_login_page(
        request=request,
        template_name="customerportal/computer/computer_login.html",
        success_url="portal:computer_dashboard",
    )


def seller_logout(request):
    seller = get_user_seller(request.user)

    if seller:
        portal_session = (
            SellerPortalSession.objects
            .filter(
                user=request.user,
                seller=seller,
                logout_at__isnull=True,
            )
            .order_by("-login_at")
            .first()
        )

        if portal_session:
            portal_session.logout_at = timezone.now()
            portal_session.last_activity_at = timezone.now()
            portal_session.save(
                update_fields=[
                    "logout_at",
                    "last_activity_at",
                ]
            )

    logout(request)
    return redirect("portal:login")


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

def _get_dashboard_month_range(month_value):
    today = timezone.localdate()

    try:
        year_text, month_text = (month_value or "").split("-", 1)
        year = int(year_text)
        month = int(month_text)
        month_start = date(year, month, 1)
    except Exception:
        year = today.year
        month = today.month
        month_start = date(year, month, 1)

    last_day = calendar.monthrange(year, month)[1]
    month_end = date(year, month, last_day)
    selected_month = f"{year:04d}-{month:02d}"

    return selected_month, month_start, month_end


@login_required
def dashboard(request):
    seller = _get_logged_in_seller(request)

    if seller is None:
        logout(request)
        return redirect("portal:login")

    selected_month, month_start, month_end = _get_dashboard_month_range(
        request.GET.get("month", "")
    )

    qs = Order.objects.filter(
        seller=seller,
        is_deleted=False,
    )

    # These statuses are shown as Pending in the seller dashboard.
    # getattr() keeps this code safe if a status constant is not defined
    # on an older version of the Order model.
    pending_statuses = {
        Order.STATUS_CREATED,
        Order.STATUS_INBOUND,
        getattr(Order, "STATUS_PROCESSING", "PROCESSING"),
        Order.STATUS_OUT_FOR_DELIVERY,
        Order.STATUS_PROVINCE_ASSIGNED,
        Order.STATUS_RETURN_ASSIGNED,
        getattr(Order, "STATUS_RETURNING", "RETURNING"),
    }

    month_qs = qs.filter(
        created_at__date__gte=month_start,
        created_at__date__lte=month_end,
    )

    month_delivered_qs = month_qs.filter(
        status=Order.STATUS_DELIVERED,
    )

    month_delivered_parcels = month_delivered_qs.count()

    month_pending_parcels = month_qs.filter(
        status__in=pending_statuses,
    ).count()

    month_balance = (
        month_delivered_qs.aggregate(total=Sum("price"))["total"] or 0
    )

    # FIX:
    # The dashboard displays only Delivery and Pending, so Done %
    # must use the same two visible totals.
    #
    # Example:
    # 54 delivered / (54 delivered + 4 pending) = 93.10%
    #
    # Returned and void orders are not included in this percentage.
    month_delivery_total = (
        month_delivered_parcels + month_pending_parcels
    )

    month_done_percent = _safe_pct(
        month_delivered_parcels,
        month_delivery_total,
    )

    today = timezone.localdate()

    today_sent_qs = qs.filter(
        created_at__date=today,
    )

    today_done_qs = qs.filter(
        status=Order.STATUS_DELIVERED,
        done_at=today,
    )

    context = {
        "seller": seller,

        # Account Summary
        "selected_month": selected_month,
        "selected_month_label": month_start.strftime("%B %Y"),
        "month_balance": month_balance,
        "month_delivered_parcels": month_delivered_parcels,
        "month_pending_parcels": month_pending_parcels,
        "month_done_percent": month_done_percent,

        # Today Summary
        "today_label": today.strftime("%d %B %Y"),
        "today_cod": (
            today_done_qs.aggregate(total=Sum("price"))["total"] or 0
        ),
        "today_sent": today_sent_qs.count(),
        "today_done": today_done_qs.count(),

        # Existing dashboard totals
        "total_parcels": qs.count(),
        "pending_parcels": qs.filter(
            status__in=pending_statuses,
        ).count(),
        "out_for_delivery": qs.filter(
            status=Order.STATUS_OUT_FOR_DELIVERY,
        ).count(),
        "delivered_parcels": qs.filter(
            status=Order.STATUS_DELIVERED,
        ).count(),
        "cod_balance": month_balance,
        "recent_orders": qs.order_by("-id")[:10],
    }

    return render(
        request,
        "customerportal/dashboard.html",
        context,
    )


# =========================================================
# STOCK
# =========================================================

@login_required
def stock(request):
    seller = _get_logged_in_seller(request)

    if seller is None:
        logout(request)
        return redirect("portal:login")

    setting = get_seller_inventory_setting(seller)

    if (
        setting.stock_mode == InventorySellerSetting.NO_STOCK
        or not setting.show_stock_in_portal
    ):
        stock_rows = []
    else:
        stock_rows = get_seller_current_stock(seller)

    return render(
        request,
        "customerportal/stock.html",
        {
            "seller": seller,
            "stock_rows": stock_rows,
        },
    )


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
# PROFILE SETTING / CHANGE PASSWORD
# =========================================================

@login_required
def change_password(request):
    seller = _get_logged_in_seller(request)

    if seller is None:
        logout(request)
        return redirect("portal:login")

    success_message = ""
    photo_error = ""

    if request.method == "POST":
        action = (request.POST.get("action") or "password").strip()

        if action == "photo":
            upload_file = request.FILES.get("profile_photo")
            form = PasswordChangeForm(request.user)

            if not upload_file:
                photo_error = "Please choose a photo first."
            else:
                try:
                    _save_seller_profile_photo(seller, upload_file)
                    return redirect("portal:change_password")
                except forms.ValidationError as e:
                    photo_error = e.messages[0] if e.messages else "Invalid photo."
                except Exception:
                    photo_error = "Cannot upload photo. Please try again."

        else:
            form = PasswordChangeForm(request.user, request.POST)

            if form.is_valid():
                user = form.save()
                update_session_auth_hash(request, user)
                success_message = "Password updated successfully."
                form = PasswordChangeForm(request.user)
    else:
        form = PasswordChangeForm(request.user)

    shop_name = (
        getattr(seller, "name", "")
        or getattr(seller, "shop_name", "")
        or getattr(seller, "seller_name", "")
        or "Seller"
    )

    return render(
        request,
        "customerportal/change_password.html",
        {
            "seller": seller,
            "form": form,
            "profile_photo_url": _get_seller_profile_photo_url(seller),
            "login_username": request.user.username,
            "shop_name": shop_name,
            "success_message": success_message,
            "photo_error": photo_error,
        },
    )


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@csrf_exempt
def telegram_update_booking(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST only"}, status=405)

    try:
        update = json.loads(request.body.decode("utf-8"))
        print("TELEGRAM UPDATE DATA:", update)

        callback = update.get("callback_query")

        if not callback:
            return JsonResponse({"success": True, "message": "No callback_query"})

        callback_id = callback.get("id")
        callback_data = (callback.get("data") or "").strip()
        from_user = callback.get("from") or {}
        message = callback.get("message") or {}

        telegram_user_id = from_user.get("id")
        telegram_name = from_user.get("first_name") or ""
        telegram_username = from_user.get("username") or ""

        print("telegram_user_id =", telegram_user_id)
        print("telegram_name =", telegram_name)
        print("telegram_username =", telegram_username)
        print("callback_data =", callback_data)

        if ":" not in callback_data:
            telegram_answer_callback(callback_id, "Invalid action")
            return JsonResponse(
                {"success": False, "error": "Invalid callback_data"},
                status=400,
            )

        action, booking_id_raw = callback_data.split(":", 1)
        action = action.strip().lower()

        try:
            booking_id = int(booking_id_raw)
        except Exception:
            telegram_answer_callback(callback_id, "Invalid booking")
            return JsonResponse(
                {"success": False, "error": "Invalid booking id"},
                status=400,
            )

        booking = SellerBooking.objects.get(id=booking_id)

        if booking.status != SellerBooking.STATUS_PENDING:
            telegram_answer_callback(callback_id, "Booking already processed")
            return JsonResponse(
                {"success": False, "error": "Booking already processed"},
                status=400,
            )

        if action == "accept":
            booking.status = get_accept_status()
            action_text = "✅ Accepted"
        elif action == "cancel":
            booking.status = get_cancel_status()
            action_text = "❌ Cancelled"
        else:
            telegram_answer_callback(callback_id, "Invalid action")
            return JsonResponse(
                {"success": False, "error": "Invalid action"},
                status=400,
            )

        if telegram_name:
            display_name = telegram_name.strip()
        elif telegram_username:
            display_name = telegram_username.strip()
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

        chat_id = booking.telegram_chat_id or message.get("chat", {}).get("id")
        message_id = booking.telegram_message_id or message.get("message_id")

        if chat_id and message_id:
            telegram_edit_message_remove_buttons(chat_id, message_id, new_text)

        telegram_answer_callback(callback_id, action_text)

        return JsonResponse(
            {
                "success": True,
                "booking_id": booking.id,
                "status": booking.status,
                "processed_by": display_name,
            }
        )

    except SellerBooking.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "Booking not found"},
            status=404,
        )
    except Exception as e:
        print("telegram_update_booking error:", e)
        return JsonResponse(
            {"success": False, "error": str(e)},
            status=500,
        )