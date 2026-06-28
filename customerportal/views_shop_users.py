from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Account

from .models import SellerPortalRole
from .permissions import (
    active_additional_user_count,
    available_user_slots,
    get_seller_account,
    is_seller_owner,
    log_portal_action,
    portal_permission_required,
    seed_default_seller_roles,
)


User = get_user_model()


def _seller_account_or_login(request):
    account = get_seller_account(request.user)
    return account


def _seller_roles(seller):
    seed_default_seller_roles(seller)
    return SellerPortalRole.objects.filter(
        seller=seller,
        is_active=True,
    ).order_by("name")


def _target_account(seller, account_id):
    return get_object_or_404(
        Account.objects.select_related(
            "user",
            "seller_role",
            "seller",
        ),
        pk=account_id,
        seller=seller,
        account_type=Account.ACCOUNT_TYPE_SELLER,
    )


def _is_protected_owner(account):
    return bool(
        account.is_seller_owner
        or (
            account.seller_id
            and account.seller.portal_user_id == account.user_id
        )
    )


def _clean_user_form(request):
    return {
        "username": (request.POST.get("username") or "").strip(),
        "first_name": (request.POST.get("first_name") or "").strip(),
        "last_name": (request.POST.get("last_name") or "").strip(),
        "email": (request.POST.get("email") or "").strip(),
        "role_id": (request.POST.get("role_id") or "").strip(),
        "is_active": request.POST.get("is_active") == "1",
    }


def _validate_role(seller, role_id):
    if not role_id or not role_id.isdigit():
        return None

    return SellerPortalRole.objects.filter(
        pk=int(role_id),
        seller=seller,
        is_active=True,
    ).first()


def _password_errors(password, user=None):
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        return list(exc.messages)
    return []


@login_required(login_url="portal:computer_login")
@portal_permission_required("users.manage")
def shop_user_list(request):
    owner_account = _seller_account_or_login(request)
    seller = owner_account.seller

    roles = _seller_roles(seller)

    accounts = list(
        Account.objects.filter(
            seller=seller,
            account_type=Account.ACCOUNT_TYPE_SELLER,
        )
        .select_related("user", "seller_role")
        .order_by("-is_seller_owner", "is_archived", "user__username")
    )

    for account in accounts:
        account.is_main_owner = _is_protected_owner(account)

    active_count = active_additional_user_count(seller)
    archived_count = sum(
        1 for account in accounts
        if not account.is_main_owner and account.is_archived
    )
    inactive_count = sum(
        1 for account in accounts
        if (
            not account.is_main_owner
            and not account.is_archived
            and not account.user.is_active
        )
    )

    return render(
        request,
        "customerportal/computer/shop_users.html",
        {
            "seller": seller,
            "accounts": accounts,
            "roles": roles,
            "active_count": active_count,
            "archived_count": archived_count,
            "inactive_count": inactive_count,
            "user_limit": seller.max_portal_users,
            "available_slots": available_user_slots(seller),
            "current_account": owner_account,
        },
    )


@login_required(login_url="portal:computer_login")
@portal_permission_required("users.manage")
@transaction.atomic
def shop_user_create(request):
    owner_account = _seller_account_or_login(request)
    seller = owner_account.seller
    roles = _seller_roles(seller)

    form_data = {
        "username": "",
        "first_name": "",
        "last_name": "",
        "email": "",
        "role_id": "",
        "is_active": True,
    }
    errors = []

    if request.method == "POST":
        form_data = _clean_user_form(request)
        password = request.POST.get("password") or ""
        password2 = request.POST.get("password2") or ""

        if not form_data["username"]:
            errors.append("Username is required.")
        elif User.objects.filter(
            username__iexact=form_data["username"],
        ).exists():
            errors.append("Username already exists.")

        role = _validate_role(seller, form_data["role_id"])
        if not role:
            errors.append("Please select a valid role.")

        if password != password2:
            errors.append("Passwords do not match.")
        else:
            pending_user = User(
                username=form_data["username"],
                email=form_data["email"],
                first_name=form_data["first_name"],
                last_name=form_data["last_name"],
            )
            errors.extend(_password_errors(password, pending_user))

        if (
            form_data["is_active"]
            and available_user_slots(seller) <= 0
        ):
            errors.append(
                f"This shop has reached its limit of "
                f"{seller.max_portal_users} active additional users."
            )

        if not errors:
            user = User.objects.create_user(
                username=form_data["username"],
                password=password,
                first_name=form_data["first_name"],
                last_name=form_data["last_name"],
                email=form_data["email"],
                is_active=form_data["is_active"],
                is_staff=False,
            )

            account = Account.objects.create(
                user=user,
                account_type=Account.ACCOUNT_TYPE_SELLER,
                seller=seller,
                seller_role=role,
                is_seller_owner=False,
                is_archived=False,
            )

            log_portal_action(
                request,
                seller,
                "USER_CREATED",
                target_user=user,
                description=f"Created seller portal user {user.username}.",
                new_value={
                    "username": user.username,
                    "role": role.name,
                    "is_active": user.is_active,
                },
            )

            messages.success(
                request,
                f"User {user.username} was created successfully.",
            )
            return redirect("portal:shop_user_list")

    return render(
        request,
        "customerportal/computer/shop_user_form.html",
        {
            "seller": seller,
            "mode": "create",
            "roles": roles,
            "form_data": form_data,
            "errors": errors,
            "target_account": None,
            "available_slots": available_user_slots(seller),
        },
    )


@login_required(login_url="portal:computer_login")
@portal_permission_required("users.manage")
@transaction.atomic
def shop_user_edit(request, account_id):
    owner_account = _seller_account_or_login(request)
    seller = owner_account.seller
    target = _target_account(seller, account_id)

    if _is_protected_owner(target):
        messages.error(request, "The main owner cannot be edited here.")
        return redirect("portal:shop_user_list")

    if target.is_archived:
        messages.error(request, "Restore this user before editing it.")
        return redirect("portal:shop_user_list")

    roles = _seller_roles(seller)
    form_data = {
        "username": target.user.username,
        "first_name": target.user.first_name,
        "last_name": target.user.last_name,
        "email": target.user.email,
        "role_id": str(target.seller_role_id or ""),
        "is_active": target.user.is_active,
    }
    errors = []

    if request.method == "POST":
        form_data = _clean_user_form(request)

        if not form_data["username"]:
            errors.append("Username is required.")
        elif User.objects.filter(
            username__iexact=form_data["username"],
        ).exclude(pk=target.user_id).exists():
            errors.append("Username already exists.")

        role = _validate_role(seller, form_data["role_id"])
        if not role:
            errors.append("Please select a valid role.")

        activating = (
            form_data["is_active"]
            and not target.user.is_active
        )

        if activating and available_user_slots(seller) <= 0:
            errors.append(
                f"This shop has reached its limit of "
                f"{seller.max_portal_users} active additional users."
            )

        if not errors:
            old_value = {
                "username": target.user.username,
                "first_name": target.user.first_name,
                "last_name": target.user.last_name,
                "email": target.user.email,
                "role": (
                    target.seller_role.name
                    if target.seller_role_id
                    else ""
                ),
                "is_active": target.user.is_active,
            }

            target.user.username = form_data["username"]
            target.user.first_name = form_data["first_name"]
            target.user.last_name = form_data["last_name"]
            target.user.email = form_data["email"]
            target.user.is_active = form_data["is_active"]
            target.user.is_staff = False
            target.user.save()

            target.seller_role = role
            target.save(update_fields=["seller_role", "updated_at"])

            log_portal_action(
                request,
                seller,
                "USER_UPDATED",
                target_user=target.user,
                description=f"Updated seller portal user {target.user.username}.",
                old_value=old_value,
                new_value={
                    "username": target.user.username,
                    "first_name": target.user.first_name,
                    "last_name": target.user.last_name,
                    "email": target.user.email,
                    "role": role.name,
                    "is_active": target.user.is_active,
                },
            )

            messages.success(
                request,
                f"User {target.user.username} was updated.",
            )
            return redirect("portal:shop_user_list")

    return render(
        request,
        "customerportal/computer/shop_user_form.html",
        {
            "seller": seller,
            "mode": "edit",
            "roles": roles,
            "form_data": form_data,
            "errors": errors,
            "target_account": target,
            "available_slots": available_user_slots(seller),
        },
    )


@login_required(login_url="portal:computer_login")
@portal_permission_required("users.reset_password")
@transaction.atomic
def shop_user_password(request, account_id):
    owner_account = _seller_account_or_login(request)
    seller = owner_account.seller
    target = _target_account(seller, account_id)

    if _is_protected_owner(target):
        messages.error(
            request,
            "The main owner password cannot be reset from Shop Users.",
        )
        return redirect("portal:shop_user_list")

    if target.is_archived:
        messages.error(request, "Archived users cannot change password.")
        return redirect("portal:shop_user_list")

    errors = []

    if request.method == "POST":
        password = request.POST.get("password") or ""
        password2 = request.POST.get("password2") or ""

        if password != password2:
            errors.append("Passwords do not match.")
        else:
            errors.extend(_password_errors(password, target.user))

        if not errors:
            target.user.set_password(password)
            target.user.save(update_fields=["password"])

            log_portal_action(
                request,
                seller,
                "PASSWORD_RESET",
                target_user=target.user,
                description=f"Reset password for {target.user.username}.",
            )

            messages.success(
                request,
                f"Password changed for {target.user.username}.",
            )
            return redirect("portal:shop_user_list")

    return render(
        request,
        "customerportal/computer/shop_user_password.html",
        {
            "seller": seller,
            "target_account": target,
            "errors": errors,
        },
    )


@login_required(login_url="portal:computer_login")
@portal_permission_required("users.manage")
@transaction.atomic
def shop_user_toggle_active(request, account_id):
    owner_account = _seller_account_or_login(request)
    seller = owner_account.seller
    target = _target_account(seller, account_id)

    if request.method != "POST":
        return redirect("portal:shop_user_list")

    if _is_protected_owner(target):
        messages.error(request, "The main owner cannot be disabled.")
        return redirect("portal:shop_user_list")

    if target.is_archived:
        messages.error(request, "Restore this user first.")
        return redirect("portal:shop_user_list")

    new_status = not target.user.is_active

    if new_status and available_user_slots(seller) <= 0:
        messages.error(
            request,
            f"User limit reached ({seller.max_portal_users}).",
        )
        return redirect("portal:shop_user_list")

    old_status = target.user.is_active
    target.user.is_active = new_status
    target.user.is_staff = False
    target.user.save(update_fields=["is_active", "is_staff"])

    log_portal_action(
        request,
        seller,
        "USER_ACTIVATED" if new_status else "USER_DISABLED",
        target_user=target.user,
        description=(
            f"{'Activated' if new_status else 'Disabled'} "
            f"{target.user.username}."
        ),
        old_value={"is_active": old_status},
        new_value={"is_active": new_status},
    )

    messages.success(
        request,
        (
            f"{target.user.username} was "
            f"{'activated' if new_status else 'disabled'}."
        ),
    )
    return redirect("portal:shop_user_list")


@login_required(login_url="portal:computer_login")
@portal_permission_required("users.manage")
@transaction.atomic
def shop_user_archive(request, account_id):
    owner_account = _seller_account_or_login(request)
    seller = owner_account.seller
    target = _target_account(seller, account_id)

    if request.method != "POST":
        return redirect("portal:shop_user_list")

    if _is_protected_owner(target):
        messages.error(request, "The main owner cannot be deleted.")
        return redirect("portal:shop_user_list")

    if target.is_archived:
        messages.info(request, "This user is already archived.")
        return redirect("portal:shop_user_list")

    target.user.is_active = False
    target.user.is_staff = False
    target.user.save(update_fields=["is_active", "is_staff"])

    target.is_archived = True
    target.archived_at = timezone.now()
    target.save(
        update_fields=[
            "is_archived",
            "archived_at",
            "updated_at",
        ]
    )

    log_portal_action(
        request,
        seller,
        "USER_ARCHIVED",
        target_user=target.user,
        description=f"Archived seller portal user {target.user.username}.",
        new_value={"is_archived": True, "is_active": False},
    )

    messages.success(
        request,
        f"{target.user.username} was deleted from active shop users.",
    )
    return redirect("portal:shop_user_list")


@login_required(login_url="portal:computer_login")
@portal_permission_required("users.manage")
@transaction.atomic
def shop_user_restore(request, account_id):
    owner_account = _seller_account_or_login(request)
    seller = owner_account.seller
    target = _target_account(seller, account_id)

    if request.method != "POST":
        return redirect("portal:shop_user_list")

    if _is_protected_owner(target):
        return redirect("portal:shop_user_list")

    if not target.is_archived:
        messages.info(request, "This user is not archived.")
        return redirect("portal:shop_user_list")

    if available_user_slots(seller) <= 0:
        messages.error(
            request,
            f"Cannot restore user. User limit reached "
            f"({seller.max_portal_users}).",
        )
        return redirect("portal:shop_user_list")

    target.is_archived = False
    target.archived_at = None
    target.save(
        update_fields=[
            "is_archived",
            "archived_at",
            "updated_at",
        ]
    )

    target.user.is_active = True
    target.user.is_staff = False
    target.user.save(update_fields=["is_active", "is_staff"])

    log_portal_action(
        request,
        seller,
        "USER_RESTORED",
        target_user=target.user,
        description=f"Restored seller portal user {target.user.username}.",
        new_value={"is_archived": False, "is_active": True},
    )

    messages.success(
        request,
        f"{target.user.username} was restored.",
    )
    return redirect("portal:shop_user_list")
