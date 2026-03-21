from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Account
from customerportal.models import SellerPortalSession
from .models import Seller, Shipper

User = get_user_model()
PER_PAGE = 50


# =============================
# CODE GENERATOR (260001)
# =============================
def _year_prefix() -> str:
    return str(timezone.localdate().year)[-2:]


def _next_year_code(model_cls) -> str:
    prefix = _year_prefix()
    max_code = (
        model_cls.objects.filter(code__startswith=prefix)
        .aggregate(m=Max("code"))
        .get("m")
    )

    if max_code and len(str(max_code)) >= 6:
        try:
            last_seq = int(str(max_code)[-4:])
        except Exception:
            last_seq = 0
    else:
        last_seq = 0

    return f"{prefix}{last_seq + 1:04d}"


def _save_with_year_code(obj, model_cls) -> bool:
    for _ in range(10):
        obj.code = _next_year_code(model_cls)
        try:
            with transaction.atomic():
                obj.save()
            return True
        except IntegrityError:
            obj.code = ""
            continue
    return False


def _apply_status(qs, status: str):
    status = (status or "all").lower()
    if status == "active":
        return qs.filter(is_active=True)
    if status == "inactive":
        return qs.filter(is_active=False)
    return qs


# =============================
# SELLER PORTAL HELPERS
# =============================
def _today_bounds():
    now = timezone.localtime()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def _month_bounds():
    now = timezone.localtime()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if now.month == 12:
        next_month = now.replace(
            year=now.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    else:
        next_month = now.replace(
            month=now.month + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    return start, next_month


def _build_seller_activity_summary(row: Seller):
    summary = {
        "portal_enabled": bool(row.portal_user_id),
        "portal_username": row.portal_user.username if row.portal_user_id else "",
        "last_login_at": None,
        "last_activity_at": None,
        "today_login_count": 0,
        "today_total_minutes": 0,
        "month_total_minutes": 0,
        "is_online_now": False,
    }

    if not row.portal_user_id:
        return summary

    sessions = SellerPortalSession.objects.filter(seller=row).order_by("-login_at")
    latest = sessions.first()

    if latest:
        summary["last_login_at"] = latest.login_at
        summary["last_activity_at"] = latest.last_activity_at

        if latest.logout_at is None and latest.last_activity_at:
            seconds = (timezone.now() - latest.last_activity_at).total_seconds()
            summary["is_online_now"] = seconds <= 300

    today_start, today_end = _today_bounds()
    today_sessions = sessions.filter(login_at__gte=today_start, login_at__lte=today_end)
    summary["today_login_count"] = today_sessions.count()
    summary["today_total_minutes"] = sum(s.duration_minutes for s in today_sessions)

    month_start, next_month = _month_bounds()
    month_sessions = sessions.filter(login_at__gte=month_start, login_at__lt=next_month)
    summary["month_total_minutes"] = sum(s.duration_minutes for s in month_sessions)

    return summary


def _get_portal_form_data(request, row: Seller | None = None):
    if request.method == "POST":
        return {
            "portal_enabled": request.POST.get("portal_enabled") == "1",
            "portal_username": (request.POST.get("portal_username") or "").strip(),
            "portal_password": (request.POST.get("portal_password") or "").strip(),
        }

    existing_username = ""
    existing_enabled = False

    if row and row.portal_user_id:
        existing_username = row.portal_user.username
        existing_enabled = True

    return {
        "portal_enabled": existing_enabled,
        "portal_username": existing_username,
        "portal_password": "",
    }


def _sync_account_record_for_seller_user(user, seller_row: Seller):
    account, _ = Account.objects.get_or_create(user=user)
    account.account_type = "seller"
    account.seller = seller_row
    account.shipper = None
    account.save()

    user.is_staff = False
    user.save(update_fields=["is_staff"])


def _disable_account_record_for_seller_user(user):
    try:
        account = user.account
    except Account.DoesNotExist:
        return

    account.account_type = "seller"
    account.seller = None
    account.shipper = None
    account.save()


def _sync_seller_portal_user(
    row: Seller,
    portal_enabled: bool,
    portal_username: str,
    portal_password: str,
):
    if not portal_enabled:
        if row.portal_user:
            user = row.portal_user
            user.is_active = False
            user.is_staff = False
            user.save(update_fields=["is_active", "is_staff"])
            _disable_account_record_for_seller_user(user)
        return

    if not portal_username:
        raise ValueError("Portal username is required when portal login is enabled.")

    existing_user = User.objects.filter(username=portal_username).exclude(id=row.portal_user_id).first()
    if existing_user:
        raise ValueError("Portal username already exists.")

    if row.portal_user:
        user = row.portal_user
        user.username = portal_username
        user.is_staff = False
        user.is_active = row.is_active

        if portal_password:
            user.set_password(portal_password)

        user.save()
    else:
        if not portal_password:
            raise ValueError("Password is required when creating a new portal account.")

        user = User.objects.create_user(
            username=portal_username,
            password=portal_password,
            is_staff=False,
            is_active=row.is_active,
        )
        row.portal_user = user
        row.save(update_fields=["portal_user"])

    if row.portal_user:
        row.portal_user.is_active = row.is_active
        row.portal_user.is_staff = False
        row.portal_user.save(update_fields=["is_active", "is_staff"])
        _sync_account_record_for_seller_user(row.portal_user, row)


# =============================
# SHIPPER PORTAL HELPERS
# =============================
def _get_shipper_portal_form_data(request, row: Shipper | None = None):
    if request.method == "POST":
        return {
            "portal_enabled": request.POST.get("portal_enabled") == "1",
            "portal_username": (request.POST.get("portal_username") or "").strip(),
            "portal_password": (request.POST.get("portal_password") or "").strip(),
        }

    existing_username = ""
    existing_enabled = False

    if row and getattr(row, "portal_user_id", None):
        existing_username = row.portal_user.username
        existing_enabled = True

    return {
        "portal_enabled": existing_enabled,
        "portal_username": existing_username,
        "portal_password": "",
    }


def _sync_account_record_for_shipper_user(user, shipper_row: Shipper):
    account, _ = Account.objects.get_or_create(user=user)
    account.account_type = "shipper"
    account.shipper = shipper_row
    account.seller = None
    account.save()

    user.is_staff = False
    user.save(update_fields=["is_staff"])


def _disable_account_record_for_shipper_user(user):
    try:
        account = user.account
    except Account.DoesNotExist:
        return

    account.account_type = "shipper"
    account.shipper = None
    account.seller = None
    account.save()


def _sync_shipper_portal_user(
    row: Shipper,
    portal_enabled: bool,
    portal_username: str,
    portal_password: str,
):
    if not hasattr(row, "portal_user"):
        return

    if not portal_enabled:
        if row.portal_user:
            user = row.portal_user
            user.is_active = False
            user.is_staff = False
            user.save(update_fields=["is_active", "is_staff"])
            _disable_account_record_for_shipper_user(user)
        return

    if not portal_username:
        raise ValueError("Portal username is required when portal login is enabled.")

    existing_user = User.objects.filter(username=portal_username).exclude(id=row.portal_user_id).first()
    if existing_user:
        raise ValueError("Portal username already exists.")

    if row.portal_user:
        user = row.portal_user
        user.username = portal_username
        user.is_staff = False
        user.is_active = row.is_active

        if portal_password:
            user.set_password(portal_password)

        user.save()
    else:
        if not portal_password:
            raise ValueError("Password is required when creating a new portal account.")

        user = User.objects.create_user(
            username=portal_username,
            password=portal_password,
            is_staff=False,
            is_active=row.is_active,
        )
        row.portal_user = user
        row.save(update_fields=["portal_user"])

    if row.portal_user:
        row.portal_user.is_active = row.is_active
        row.portal_user.is_staff = False
        row.portal_user.save(update_fields=["is_active", "is_staff"])
        _sync_account_record_for_shipper_user(row.portal_user, row)


# =============================
# SELLER
# =============================
@login_required
def seller_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "all").strip().lower()
    search = request.GET.get("search") == "1"
    page_number = request.GET.get("page")

    if not search:
        qs = Seller.objects.none()
    else:
        qs = Seller.objects.select_related("portal_user").all().order_by("-id")
        qs = _apply_status(qs, status)

        if q:
            qs = qs.filter(
                Q(code__icontains=q)
                | Q(name__icontains=q)
                | Q(phone__icontains=q)
                | Q(address__icontains=q)
                | Q(portal_user__username__icontains=q)
            )

    paginator = Paginator(qs, PER_PAGE)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "masterdata/seller_list.html",
        {
            "q": q,
            "status": status,
            "search": search,
            "page_obj": page_obj,
        },
    )


@login_required
def seller_create(request):
    row = Seller(is_active=True)
    error = ""
    portal_data = _get_portal_form_data(request, row=None)

    if request.method == "POST":
        row.name = (request.POST.get("name") or "").strip()
        row.phone = (request.POST.get("phone") or "").strip()
        row.address = (request.POST.get("address") or "").strip()
        row.is_active = request.POST.get("is_active") == "1"

        if not row.name:
            error = "Name is required."
            return render(
                request,
                "masterdata/seller_form.html",
                {
                    "row": row,
                    "mode": "create",
                    "portal_data": portal_data,
                    "activity": None,
                    "error": error,
                },
            )

        try:
            with transaction.atomic():
                ok = _save_with_year_code(row, Seller)
                if not ok:
                    raise ValueError("Cannot generate unique seller code.")

                _sync_seller_portal_user(
                    row=row,
                    portal_enabled=portal_data["portal_enabled"],
                    portal_username=portal_data["portal_username"],
                    portal_password=portal_data["portal_password"],
                )

        except ValueError as e:
            error = str(e)
            return render(
                request,
                "masterdata/seller_form.html",
                {
                    "row": row,
                    "mode": "create",
                    "portal_data": portal_data,
                    "activity": None,
                    "error": error,
                },
            )

        messages.success(request, "Seller created successfully.")
        return redirect("seller_edit", pk=row.pk)

    return render(
        request,
        "masterdata/seller_form.html",
        {
            "row": row,
            "mode": "create",
            "portal_data": portal_data,
            "activity": None,
            "error": error,
        },
    )


@login_required
def seller_edit(request, pk: int):
    row = get_object_or_404(Seller.objects.select_related("portal_user"), pk=pk)
    error = ""

    if request.method == "POST":
        portal_data = _get_portal_form_data(request, row=row)

        row.name = (request.POST.get("name") or "").strip()
        row.phone = (request.POST.get("phone") or "").strip()
        row.address = (request.POST.get("address") or "").strip()
        row.is_active = request.POST.get("is_active") == "1"

        if not row.name:
            error = "Name is required."
            return render(
                request,
                "masterdata/seller_form.html",
                {
                    "row": row,
                    "mode": "edit",
                    "portal_data": portal_data,
                    "activity": _build_seller_activity_summary(row),
                    "error": error,
                },
            )

        try:
            with transaction.atomic():
                row.save()
                _sync_seller_portal_user(
                    row=row,
                    portal_enabled=portal_data["portal_enabled"],
                    portal_username=portal_data["portal_username"],
                    portal_password=portal_data["portal_password"],
                )
        except ValueError as e:
            error = str(e)
            return render(
                request,
                "masterdata/seller_form.html",
                {
                    "row": row,
                    "mode": "edit",
                    "portal_data": portal_data,
                    "activity": _build_seller_activity_summary(row),
                    "error": error,
                },
            )

        messages.success(request, "Seller updated successfully.")
        return redirect("seller_edit", pk=row.pk)

    portal_data = _get_portal_form_data(request, row=row)

    return render(
        request,
        "masterdata/seller_form.html",
        {
            "row": row,
            "mode": "edit",
            "portal_data": portal_data,
            "activity": _build_seller_activity_summary(row),
            "error": error,
        },
    )


@login_required
@transaction.atomic
def seller_delete(request, pk: int):
    row = get_object_or_404(Seller.objects.select_related("portal_user"), pk=pk)

    if request.method == "POST":
        portal_user = row.portal_user
        row.delete()

        if portal_user:
            portal_user.is_active = False
            portal_user.is_staff = False
            portal_user.save(update_fields=["is_active", "is_staff"])
            _disable_account_record_for_seller_user(portal_user)

        messages.success(request, "Seller deleted.")

    return redirect("seller_list")


@login_required
@transaction.atomic
def seller_toggle_active(request, pk: int):
    row = get_object_or_404(Seller.objects.select_related("portal_user"), pk=pk)

    if request.method == "POST":
        row.is_active = not row.is_active
        row.save(update_fields=["is_active"])

        if row.portal_user:
            row.portal_user.is_active = row.is_active
            row.portal_user.is_staff = False
            row.portal_user.save(update_fields=["is_active", "is_staff"])

        messages.success(request, f"Seller {'activated' if row.is_active else 'deactivated'}.")

    next_url = request.POST.get("next") or ""

    if next_url.startswith("?"):
        return redirect(request.path.replace(f"{pk}/toggle-active/", "") + next_url)
    if next_url.startswith("/"):
        return redirect(next_url)

    return redirect("seller_list")


# =============================
# SHIPPER
# =============================
@login_required
def shipper_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "all").strip().lower()
    search = request.GET.get("search") == "1"
    page_number = request.GET.get("page")

    if not search:
        qs = Shipper.objects.none()
    else:
        if hasattr(Shipper, "portal_user"):
            qs = Shipper.objects.select_related("portal_user").all().order_by("-id")
        else:
            qs = Shipper.objects.all().order_by("-id")

        qs = _apply_status(qs, status)

        if q:
            query = (
                Q(code__icontains=q)
                | Q(name__icontains=q)
                | Q(phone__icontains=q)
            )
            if hasattr(Shipper, "portal_user"):
                query |= Q(portal_user__username__icontains=q)
            qs = qs.filter(query)

    paginator = Paginator(qs, PER_PAGE)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "masterdata/shipper_list.html",
        {
            "q": q,
            "status": status,
            "search": search,
            "page_obj": page_obj,
        },
    )


@login_required
def shipper_create(request):
    row = Shipper(is_active=True)
    error = ""

    if hasattr(Shipper, "portal_user"):
        portal_data = _get_shipper_portal_form_data(request, row=None)
    else:
        portal_data = None

    if request.method == "POST":
        row.name = (request.POST.get("name") or "").strip()
        row.phone = (request.POST.get("phone") or "").strip()
        row.shipper_type = (request.POST.get("shipper_type") or "DELIVERY").strip()
        row.is_active = request.POST.get("is_active") == "1"

        if not row.name:
            error = "Name is required."
            return render(
                request,
                "masterdata/shipper_form.html",
                {
                    "row": row,
                    "mode": "create",
                    "portal_data": portal_data,
                    "error": error,
                },
            )

        try:
            with transaction.atomic():
                ok = _save_with_year_code(row, Shipper)
                if not ok:
                    raise ValueError("Cannot generate unique shipper code.")

                if hasattr(Shipper, "portal_user"):
                    _sync_shipper_portal_user(
                        row=row,
                        portal_enabled=portal_data["portal_enabled"],
                        portal_username=portal_data["portal_username"],
                        portal_password=portal_data["portal_password"],
                    )
        except ValueError as e:
            error = str(e)
            return render(
                request,
                "masterdata/shipper_form.html",
                {
                    "row": row,
                    "mode": "create",
                    "portal_data": portal_data,
                    "error": error,
                },
            )

        messages.success(request, "Shipper created successfully.")
        return redirect("shipper_edit", pk=row.pk)

    return render(
        request,
        "masterdata/shipper_form.html",
        {
            "row": row,
            "mode": "create",
            "portal_data": portal_data,
            "error": error,
        },
    )


@login_required
def shipper_edit(request, pk: int):
    if hasattr(Shipper, "portal_user"):
        row = get_object_or_404(Shipper.objects.select_related("portal_user"), pk=pk)
    else:
        row = get_object_or_404(Shipper, pk=pk)

    error = ""

    if request.method == "POST":
        row.name = (request.POST.get("name") or "").strip()
        row.phone = (request.POST.get("phone") or "").strip()
        row.shipper_type = (request.POST.get("shipper_type") or "DELIVERY").strip()
        row.is_active = request.POST.get("is_active") == "1"

        if hasattr(Shipper, "portal_user"):
            portal_data = _get_shipper_portal_form_data(request, row=row)
        else:
            portal_data = None

        if not row.name:
            error = "Name is required."
            return render(
                request,
                "masterdata/shipper_form.html",
                {
                    "row": row,
                    "mode": "edit",
                    "portal_data": portal_data,
                    "error": error,
                },
            )

        try:
            with transaction.atomic():
                row.save()

                if hasattr(Shipper, "portal_user"):
                    _sync_shipper_portal_user(
                        row=row,
                        portal_enabled=portal_data["portal_enabled"],
                        portal_username=portal_data["portal_username"],
                        portal_password=portal_data["portal_password"],
                    )
        except ValueError as e:
            error = str(e)
            return render(
                request,
                "masterdata/shipper_form.html",
                {
                    "row": row,
                    "mode": "edit",
                    "portal_data": portal_data,
                    "error": error,
                },
            )

        messages.success(request, "Shipper updated successfully.")
        return redirect("shipper_edit", pk=row.pk)

    if hasattr(Shipper, "portal_user"):
        portal_data = _get_shipper_portal_form_data(request, row=row)
    else:
        portal_data = None

    return render(
        request,
        "masterdata/shipper_form.html",
        {
            "row": row,
            "mode": "edit",
            "portal_data": portal_data,
            "error": error,
        },
    )


@login_required
@transaction.atomic
def shipper_delete(request, pk: int):
    if hasattr(Shipper, "portal_user"):
        row = get_object_or_404(Shipper.objects.select_related("portal_user"), pk=pk)
    else:
        row = get_object_or_404(Shipper, pk=pk)

    if request.method == "POST":
        portal_user = getattr(row, "portal_user", None)
        row.delete()

        if portal_user:
            portal_user.is_active = False
            portal_user.is_staff = False
            portal_user.save(update_fields=["is_active", "is_staff"])
            _disable_account_record_for_shipper_user(portal_user)

        messages.success(request, "Shipper deleted.")

    return redirect("shipper_list")


@login_required
@transaction.atomic
def shipper_toggle_active(request, pk: int):
    if hasattr(Shipper, "portal_user"):
        row = get_object_or_404(Shipper.objects.select_related("portal_user"), pk=pk)
    else:
        row = get_object_or_404(Shipper, pk=pk)

    if request.method == "POST":
        row.is_active = not row.is_active
        row.save(update_fields=["is_active"])

        portal_user = getattr(row, "portal_user", None)
        if portal_user:
            portal_user.is_active = row.is_active
            portal_user.is_staff = False
            portal_user.save(update_fields=["is_active", "is_staff"])

        messages.success(request, f"Shipper {'activated' if row.is_active else 'deactivated'}.")

    next_url = request.POST.get("next") or ""

    if next_url.startswith("?"):
        return redirect(request.path.replace(f"{pk}/toggle-active/", "") + next_url)
    if next_url.startswith("/"):
        return redirect(next_url)

    return redirect("shipper_list")


# =============================
# SELLER AUTOCOMPLETE API
# =============================
@login_required
def seller_autocomplete(request):
    q = (request.GET.get("q") or "").strip()
    results = []

    if q:
        sellers = (
            Seller.objects.filter(is_active=True)
            .filter(
                Q(code__icontains=q)
                | Q(name__icontains=q)
                | Q(phone__icontains=q)
            )
            .order_by("name")[:10]
        )

        for s in sellers:
            results.append(
                {
                    "id": s.id,
                    "code": s.code,
                    "name": s.name,
                    "phone": s.phone or "",
                }
            )

    return JsonResponse({"items": results})