from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    BigIntegerField,
    Count,
    DateTimeField,
    IntegerField,
    Max,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Account
from customerportal.models import (
    SellerPortalDailyUsage,
    SellerPortalPageUsage,
    SellerPortalRole,
    SellerPortalSession,
)
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


def _seconds_to_minutes(seconds):
    return max(int((seconds or 0) // 60), 0)


def _parse_max_portal_users(request, current_value=5):
    raw_value = request.POST.get("max_portal_users")

    if raw_value is None:
        return max(int(current_value or 0), 0)

    raw_value = str(raw_value).strip()

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError("Maximum portal users must be a whole number.")

    if value < 0:
        raise ValueError("Maximum portal users cannot be negative.")

    if value > 1000:
        raise ValueError("Maximum portal users cannot be greater than 1000.")

    return value


def _seller_accounts(row: Seller):
    accounts = list(
        Account.objects.filter(
            seller=row,
            account_type=Account.ACCOUNT_TYPE_SELLER,
        )
        .select_related("user", "seller_role")
        .order_by("-is_seller_owner", "is_archived", "user__username")
    )

    for account in accounts:
        account.is_main_owner = bool(
            account.is_seller_owner
            or row.portal_user_id == account.user_id
        )

    return accounts


def _build_seller_activity_summary(row: Seller):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    online_cutoff = timezone.now() - timedelta(minutes=5)

    accounts = _seller_accounts(row)
    additional_accounts = [a for a in accounts if not a.is_main_owner]

    active_additional_count = sum(
        1
        for account in additional_accounts
        if account.user.is_active and not account.is_archived
    )
    archived_count = sum(
        1 for account in additional_accounts if account.is_archived
    )
    disabled_count = sum(
        1
        for account in additional_accounts
        if not account.is_archived and not account.user.is_active
    )

    usage_qs = SellerPortalDailyUsage.objects.filter(seller=row)
    today_usage = usage_qs.filter(usage_date=today).aggregate(
        active_seconds=Coalesce(
            Sum("active_seconds"),
            Value(0),
            output_field=BigIntegerField(),
        ),
        page_views=Coalesce(
            Sum("page_views"),
            Value(0),
            output_field=IntegerField(),
        ),
        active_users=Count("user", distinct=True),
        last_seen_at=Max("last_seen_at"),
    )
    month_usage = usage_qs.filter(
        usage_date__gte=month_start,
        usage_date__lte=today,
    ).aggregate(
        active_seconds=Coalesce(
            Sum("active_seconds"),
            Value(0),
            output_field=BigIntegerField(),
        ),
        page_views=Coalesce(
            Sum("page_views"),
            Value(0),
            output_field=IntegerField(),
        ),
        active_users=Count("user", distinct=True),
    )

    sessions = (
        SellerPortalSession.objects.filter(seller=row)
        .select_related("user")
        .order_by("-login_at")
    )
    latest_session = sessions.first()

    online_user_ids = list(
        sessions.filter(
            logout_at__isnull=True,
            last_activity_at__gte=online_cutoff,
        )
        .values_list("user_id", flat=True)
        .distinct()
    )

    top_pages = list(
        SellerPortalPageUsage.objects.filter(
            daily_usage__seller=row,
            daily_usage__usage_date__gte=month_start,
            daily_usage__usage_date__lte=today,
        )
        .values("page_key", "page_name")
        .annotate(
            active_seconds=Coalesce(
                Sum("active_seconds"),
                Value(0),
                output_field=BigIntegerField(),
            ),
            page_views=Coalesce(
                Sum("page_views"),
                Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("-active_seconds", "-page_views")[:8]
    )

    for page in top_pages:
        page["active_minutes"] = _seconds_to_minutes(page["active_seconds"])

    recent_sessions = list(sessions[:10])

    summary = {
        "portal_enabled": bool(row.portal_user_id and row.portal_user.is_active),
        "portal_username": row.portal_user.username if row.portal_user_id else "",
        "last_login_at": latest_session.login_at if latest_session else None,
        "last_activity_at": (
            latest_session.last_activity_at if latest_session else None
        ),
        "today_login_count": sessions.filter(login_at__date=today).count(),
        "today_total_minutes": _seconds_to_minutes(today_usage["active_seconds"]),
        "today_page_views": today_usage["page_views"],
        "today_active_users": today_usage["active_users"],
        "month_total_minutes": _seconds_to_minutes(month_usage["active_seconds"]),
        "month_page_views": month_usage["page_views"],
        "month_active_users": month_usage["active_users"],
        "is_online_now": bool(online_user_ids),
        "online_count": len(online_user_ids),
        "owner_count": 1 if row.portal_user_id else 0,
        "active_additional_count": active_additional_count,
        "archived_count": archived_count,
        "disabled_count": disabled_count,
        "available_slots": max(
            int(row.max_portal_users or 0) - active_additional_count,
            0,
        ),
        "total_linked_accounts": len(accounts),
        "role_count": SellerPortalRole.objects.filter(seller=row).count(),
        "accounts": accounts,
        "top_pages": top_pages,
        "recent_sessions": recent_sessions,
    }

    return summary


def _seller_list_queryset():
    today = timezone.localdate()
    online_cutoff = timezone.now() - timedelta(minutes=5)

    active_users_subquery = (
        Account.objects.filter(
            seller=OuterRef("pk"),
            account_type=Account.ACCOUNT_TYPE_SELLER,
            is_seller_owner=False,
            is_archived=False,
            user__is_active=True,
        )
        .values("seller")
        .annotate(total=Count("id"))
        .values("total")[:1]
    )

    archived_users_subquery = (
        Account.objects.filter(
            seller=OuterRef("pk"),
            account_type=Account.ACCOUNT_TYPE_SELLER,
            is_seller_owner=False,
            is_archived=True,
        )
        .values("seller")
        .annotate(total=Count("id"))
        .values("total")[:1]
    )

    today_usage_subquery = (
        SellerPortalDailyUsage.objects.filter(
            seller=OuterRef("pk"),
            usage_date=today,
        )
        .values("seller")
        .annotate(total=Sum("active_seconds"))
        .values("total")[:1]
    )

    online_users_subquery = (
        SellerPortalSession.objects.filter(
            seller=OuterRef("pk"),
            logout_at__isnull=True,
            last_activity_at__gte=online_cutoff,
        )
        .values("seller")
        .annotate(total=Count("user", distinct=True))
        .values("total")[:1]
    )

    latest_activity_subquery = (
        SellerPortalSession.objects.filter(seller=OuterRef("pk"))
        .order_by("-last_activity_at")
        .values("last_activity_at")[:1]
    )

    return Seller.objects.select_related("portal_user").annotate(
        active_additional_users=Coalesce(
            Subquery(active_users_subquery, output_field=IntegerField()),
            Value(0),
            output_field=IntegerField(),
        ),
        archived_portal_users=Coalesce(
            Subquery(archived_users_subquery, output_field=IntegerField()),
            Value(0),
            output_field=IntegerField(),
        ),
        today_active_seconds=Coalesce(
            Subquery(today_usage_subquery, output_field=BigIntegerField()),
            Value(0),
            output_field=BigIntegerField(),
        ),
        online_portal_users=Coalesce(
            Subquery(online_users_subquery, output_field=IntegerField()),
            Value(0),
            output_field=IntegerField(),
        ),
        portal_last_activity_at=Subquery(
            latest_activity_subquery,
            output_field=DateTimeField(),
        ),
    )


def _close_open_seller_sessions(row: Seller):
    now = timezone.now()
    SellerPortalSession.objects.filter(
        seller=row,
        logout_at__isnull=True,
    ).update(logout_at=now, last_activity_at=now)


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
        existing_enabled = row.portal_user.is_active

    return {
        "portal_enabled": existing_enabled,
        "portal_username": existing_username,
        "portal_password": "",
    }


def _sync_account_record_for_seller_user(user, seller_row: Seller):
    account, _ = Account.objects.get_or_create(user=user)
    account.account_type = Account.ACCOUNT_TYPE_SELLER
    account.seller = seller_row
    account.shipper = None
    account.seller_role = None
    account.is_seller_owner = True
    account.is_archived = False
    account.archived_at = None
    account.save()

    user.is_staff = False
    user.save(update_fields=["is_staff"])


def _disable_account_record_for_seller_user(user, seller_row: Seller | None = None):
    try:
        account = user.account
    except Account.DoesNotExist:
        return

    account.account_type = Account.ACCOUNT_TYPE_SELLER
    account.shipper = None

    if seller_row is not None:
        account.seller = seller_row
        account.seller_role = None
        account.is_seller_owner = True
    else:
        account.seller = None
        account.seller_role = None
        account.is_seller_owner = False

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
            _disable_account_record_for_seller_user(user, row)
            _close_open_seller_sessions(row)
        return

    if not portal_username:
        raise ValueError("Portal username is required when portal login is enabled.")

    existing_user = (
        User.objects.filter(username__iexact=portal_username)
        .exclude(id=row.portal_user_id)
        .first()
    )
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

        if not row.is_active:
            _close_open_seller_sessions(row)


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
        qs = _seller_list_queryset().order_by("-id")
        qs = _apply_status(qs, status)

        if q:
            qs = qs.filter(
                Q(code__icontains=q)
                | Q(name__icontains=q)
                | Q(phone__icontains=q)
                | Q(address__icontains=q)
                | Q(portal_user__username__icontains=q)
                | Q(account_rows__user__username__icontains=q)
            ).distinct()

    paginator = Paginator(qs, PER_PAGE)
    page_obj = paginator.get_page(page_number)

    for seller in page_obj.object_list:
        seller.today_active_minutes = _seconds_to_minutes(
            seller.today_active_seconds
        )
        seller.available_portal_slots = max(
            int(seller.max_portal_users or 0)
            - int(seller.active_additional_users or 0),
            0,
        )

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
    row = Seller(is_active=True, max_portal_users=5)
    error = ""
    portal_data = _get_portal_form_data(request, row=None)

    if request.method == "POST":
        row.name = (request.POST.get("name") or "").strip()
        row.phone = (request.POST.get("phone") or "").strip()
        row.address = (request.POST.get("address") or "").strip()
        row.is_active = request.POST.get("is_active") == "1"

        try:
            row.max_portal_users = _parse_max_portal_users(
                request,
                current_value=5,
            )
        except ValueError as exc:
            error = str(exc)

        if not row.name and not error:
            error = "Name is required."

        if error:
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

        except ValueError as exc:
            error = str(exc)
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
    row = get_object_or_404(
        Seller.objects.select_related("portal_user"),
        pk=pk,
    )
    error = ""

    if request.method == "POST":
        portal_data = _get_portal_form_data(request, row=row)

        row.name = (request.POST.get("name") or "").strip()
        row.phone = (request.POST.get("phone") or "").strip()
        row.address = (request.POST.get("address") or "").strip()
        row.is_active = request.POST.get("is_active") == "1"

        try:
            row.max_portal_users = _parse_max_portal_users(
                request,
                current_value=row.max_portal_users,
            )
        except ValueError as exc:
            error = str(exc)

        if not row.name and not error:
            error = "Name is required."

        if error:
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

                if not row.is_active:
                    _close_open_seller_sessions(row)

        except ValueError as exc:
            error = str(exc)
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
    row = get_object_or_404(
        Seller.objects.select_related("portal_user"),
        pk=pk,
    )

    if request.method == "POST":
        portal_user = row.portal_user
        _close_open_seller_sessions(row)
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
    row = get_object_or_404(
        Seller.objects.select_related("portal_user"),
        pk=pk,
    )

    if request.method == "POST":
        row.is_active = not row.is_active
        row.save(update_fields=["is_active"])

        # Seller inactivity is checked for every portal account. Therefore all
        # linked sub-users are blocked without overwriting their individual
        # disabled/active choices. Only the protected owner mirrors seller state.
        if row.portal_user:
            row.portal_user.is_active = row.is_active
            row.portal_user.is_staff = False
            row.portal_user.save(update_fields=["is_active", "is_staff"])
            _sync_account_record_for_seller_user(row.portal_user, row)

        if not row.is_active:
            _close_open_seller_sessions(row)

        messages.success(
            request,
            f"Seller {'activated' if row.is_active else 'deactivated'}. "
            f"All linked portal users are "
            f"{'allowed again' if row.is_active else 'blocked'}.",
        )

    next_url = request.POST.get("next") or ""

    if next_url.startswith("?"):
        return redirect(
            request.path.replace(f"{pk}/toggle-active/", "") + next_url
        )
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