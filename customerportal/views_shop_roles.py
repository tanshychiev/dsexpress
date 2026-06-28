from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .models import SellerPortalRole
from .permissions import (
    PERMISSION_GROUPS,
    get_seller_account,
    log_portal_action,
    permissions_from_post,
    portal_permission_required,
    seed_default_seller_roles,
)


def _role_for_seller(seller, role_id):
    return get_object_or_404(
        SellerPortalRole,
        pk=role_id,
        seller=seller,
    )


def _clean_role_form(request):
    return {
        "name": (request.POST.get("name") or "").strip(),
        "description": (
            request.POST.get("description") or ""
        ).strip(),
        "is_active": request.POST.get("is_active") == "1",
    }


@login_required(login_url="portal:computer_login")
@portal_permission_required("roles.manage")
def shop_role_list(request):
    account = get_seller_account(request.user)
    seller = account.seller

    seed_default_seller_roles(seller)

    roles = list(
        SellerPortalRole.objects.filter(seller=seller)
        .annotate(
            assigned_user_count=Count(
                "accounts",
                filter=Q(
                    accounts__is_archived=False,
                    accounts__user__is_active=True,
                ),
            )
        )
        .order_by("-is_recommended", "name")
    )

    for role in roles:
        role.permission_count = sum(
            1
            for value in (role.permissions or {}).values()
            if value
        )

    return render(
        request,
        "customerportal/computer/shop_roles.html",
        {
            "seller": seller,
            "roles": roles,
        },
    )


@login_required(login_url="portal:computer_login")
@portal_permission_required("roles.manage")
@transaction.atomic
def shop_role_create(request):
    account = get_seller_account(request.user)
    seller = account.seller

    form_data = {
        "name": "",
        "description": "",
        "is_active": True,
    }
    selected_permissions = {"dashboard.view": True}
    errors = []

    if request.method == "POST":
        form_data = _clean_role_form(request)
        selected_permissions = permissions_from_post(request.POST)

        if not form_data["name"]:
            errors.append("Role name is required.")
        elif SellerPortalRole.objects.filter(
            seller=seller,
            name__iexact=form_data["name"],
        ).exists():
            errors.append("A role with this name already exists.")

        if not errors:
            role = SellerPortalRole.objects.create(
                seller=seller,
                name=form_data["name"],
                description=form_data["description"],
                permissions=selected_permissions,
                is_recommended=False,
                is_active=form_data["is_active"],
            )

            log_portal_action(
                request,
                seller,
                "ROLE_CREATED",
                description=f"Created role {role.name}.",
                new_value={
                    "name": role.name,
                    "description": role.description,
                    "permissions": role.permissions,
                    "is_active": role.is_active,
                },
            )

            messages.success(
                request,
                f"Role {role.name} was created.",
            )
            return redirect("portal:shop_role_list")

    return render(
        request,
        "customerportal/computer/shop_role_form.html",
        {
            "seller": seller,
            "mode": "create",
            "role": None,
            "form_data": form_data,
            "permission_groups": PERMISSION_GROUPS,
            "selected_permissions": selected_permissions,
            "errors": errors,
        },
    )


@login_required(login_url="portal:computer_login")
@portal_permission_required("roles.manage")
@transaction.atomic
def shop_role_edit(request, role_id):
    account = get_seller_account(request.user)
    seller = account.seller
    role = _role_for_seller(seller, role_id)

    form_data = {
        "name": role.name,
        "description": role.description,
        "is_active": role.is_active,
    }
    selected_permissions = dict(role.permissions or {})
    errors = []

    if request.method == "POST":
        form_data = _clean_role_form(request)
        selected_permissions = permissions_from_post(request.POST)

        if not form_data["name"]:
            errors.append("Role name is required.")
        elif SellerPortalRole.objects.filter(
            seller=seller,
            name__iexact=form_data["name"],
        ).exclude(pk=role.pk).exists():
            errors.append("A role with this name already exists.")

        if not errors:
            old_value = {
                "name": role.name,
                "description": role.description,
                "permissions": role.permissions,
                "is_active": role.is_active,
            }

            role.name = form_data["name"]
            role.description = form_data["description"]
            role.permissions = selected_permissions
            role.is_active = form_data["is_active"]
            role.save()

            log_portal_action(
                request,
                seller,
                "ROLE_UPDATED",
                description=f"Updated role {role.name}.",
                old_value=old_value,
                new_value={
                    "name": role.name,
                    "description": role.description,
                    "permissions": role.permissions,
                    "is_active": role.is_active,
                },
            )

            messages.success(
                request,
                f"Role {role.name} was updated.",
            )
            return redirect("portal:shop_role_list")

    return render(
        request,
        "customerportal/computer/shop_role_form.html",
        {
            "seller": seller,
            "mode": "edit",
            "role": role,
            "form_data": form_data,
            "permission_groups": PERMISSION_GROUPS,
            "selected_permissions": selected_permissions,
            "errors": errors,
        },
    )


@login_required(login_url="portal:computer_login")
@portal_permission_required("roles.manage")
@transaction.atomic
def shop_role_duplicate(request, role_id):
    account = get_seller_account(request.user)
    seller = account.seller
    role = _role_for_seller(seller, role_id)

    if request.method != "POST":
        return redirect("portal:shop_role_list")

    base_name = f"{role.name} Copy"
    new_name = base_name
    counter = 2

    while SellerPortalRole.objects.filter(
        seller=seller,
        name__iexact=new_name,
    ).exists():
        new_name = f"{base_name} {counter}"
        counter += 1

    copied = SellerPortalRole.objects.create(
        seller=seller,
        name=new_name,
        description=role.description,
        permissions=dict(role.permissions or {}),
        is_recommended=False,
        is_active=True,
    )

    log_portal_action(
        request,
        seller,
        "ROLE_DUPLICATED",
        description=f"Duplicated role {role.name} as {copied.name}.",
        new_value={
            "source_role": role.name,
            "new_role": copied.name,
        },
    )

    messages.success(
        request,
        f"Role copied as {copied.name}.",
    )
    return redirect("portal:shop_role_edit", role_id=copied.id)


@login_required(login_url="portal:computer_login")
@portal_permission_required("roles.manage")
@transaction.atomic
def shop_role_toggle_active(request, role_id):
    account = get_seller_account(request.user)
    seller = account.seller
    role = _role_for_seller(seller, role_id)

    if request.method != "POST":
        return redirect("portal:shop_role_list")

    old_status = role.is_active
    role.is_active = not role.is_active
    role.save(update_fields=["is_active", "updated_at"])

    log_portal_action(
        request,
        seller,
        "ROLE_ACTIVATED" if role.is_active else "ROLE_DISABLED",
        description=(
            f"{'Activated' if role.is_active else 'Disabled'} "
            f"role {role.name}."
        ),
        old_value={"is_active": old_status},
        new_value={"is_active": role.is_active},
    )

    messages.success(
        request,
        f"Role {role.name} was "
        f"{'activated' if role.is_active else 'disabled'}.",
    )
    return redirect("portal:shop_role_list")


@login_required(login_url="portal:computer_login")
@portal_permission_required("roles.manage")
@transaction.atomic
def shop_role_delete(request, role_id):
    account = get_seller_account(request.user)
    seller = account.seller
    role = _role_for_seller(seller, role_id)

    if request.method != "POST":
        return redirect("portal:shop_role_list")

    if role.is_recommended:
        messages.error(
            request,
            "Recommended roles cannot be deleted. "
            "You can edit or disable them.",
        )
        return redirect("portal:shop_role_list")

    if role.accounts.exists():
        messages.error(
            request,
            "This role is assigned to users. "
            "Move those users to another role first.",
        )
        return redirect("portal:shop_role_list")

    old_value = {
        "name": role.name,
        "description": role.description,
        "permissions": role.permissions,
        "is_active": role.is_active,
    }
    role_name = role.name
    role.delete()

    log_portal_action(
        request,
        seller,
        "ROLE_DELETED",
        description=f"Deleted role {role_name}.",
        old_value=old_value,
    )

    messages.success(request, f"Role {role_name} was deleted.")
    return redirect("portal:shop_role_list")
