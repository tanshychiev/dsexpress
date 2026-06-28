from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.models import Account
from .models import SellerPortalAuditLog, SellerPortalRole


# ============================================================
# SELLER PORTAL PERMISSIONS
# ============================================================

PERMISSION_GROUPS = [
    {
        "name": "General",
        "permissions": [
            ("dashboard.view", "View Dashboard"),
        ],
    },
    {
        "name": "Orders",
        "permissions": [
            ("orders.view", "View Orders"),
            ("orders.create", "Create Orders"),
            ("orders.edit", "Edit Orders"),
            ("orders.cancel", "Cancel Orders"),
            ("orders.export", "Export Orders"),
        ],
    },
    {
        "name": "Reports",
        "permissions": [
            ("delivery_report.view", "View Delivery Report"),
            ("cod_report.view", "View COD Report"),
            ("province_cod_report.view", "View Province COD Report"),
            ("reports.export", "Export Reports"),
        ],
    },
    {
        "name": "Inventory",
        "permissions": [
            ("inventory.view", "View Inventory"),
            ("inventory.manage", "Manage Inventory"),
        ],
    },
    {
        "name": "Shop Management",
        "permissions": [
            ("users.manage", "Manage Shop Users"),
            ("roles.manage", "Manage Roles & Permissions"),
            ("users.reset_password", "Reset User Passwords"),
        ],
    },
]

ALL_PERMISSION_KEYS = {
    permission_key
    for group in PERMISSION_GROUPS
    for permission_key, _label in group["permissions"]
}


DEFAULT_ROLE_TEMPLATES = [
    {
        "name": "Manager",
        "description": "Full shop management except changing the owner or user limit.",
        "permissions": {
            "dashboard.view": True,
            "orders.view": True,
            "orders.create": True,
            "orders.edit": True,
            "orders.cancel": True,
            "orders.export": True,
            "delivery_report.view": True,
            "cod_report.view": True,
            "province_cod_report.view": True,
            "reports.export": True,
            "inventory.view": True,
            "inventory.manage": True,
            "users.manage": True,
            "roles.manage": True,
            "users.reset_password": True,
        },
    },
    {
        "name": "Operations",
        "description": "Orders, delivery reports, Province COD and inventory operations.",
        "permissions": {
            "dashboard.view": True,
            "orders.view": True,
            "orders.create": True,
            "orders.edit": True,
            "orders.cancel": False,
            "orders.export": True,
            "delivery_report.view": True,
            "cod_report.view": False,
            "province_cod_report.view": True,
            "reports.export": True,
            "inventory.view": True,
            "inventory.manage": True,
            "users.manage": False,
            "roles.manage": False,
            "users.reset_password": False,
        },
    },
    {
        "name": "Viewer / Accountant",
        "description": "View orders, reports, COD and inventory without operational editing.",
        "permissions": {
            "dashboard.view": True,
            "orders.view": True,
            "orders.create": False,
            "orders.edit": False,
            "orders.cancel": False,
            "orders.export": True,
            "delivery_report.view": True,
            "cod_report.view": True,
            "province_cod_report.view": True,
            "reports.export": True,
            "inventory.view": True,
            "inventory.manage": False,
            "users.manage": False,
            "roles.manage": False,
            "users.reset_password": False,
        },
    },
]


def get_seller_account(user):
    """
    Return the valid seller Account for a logged-in portal user.

    Staff users, archived accounts, inactive users and inactive sellers
    are rejected.
    """
    if not user or not user.is_authenticated:
        return None

    if user.is_staff or not user.is_active:
        return None

    account = getattr(user, "account", None)

    if not account:
        return None

    if account.account_type != Account.ACCOUNT_TYPE_SELLER:
        return None

    if not account.seller_id or account.is_archived:
        return None

    seller = account.seller

    if not seller or not seller.is_active:
        return None

    return account


def get_user_seller(user):
    account = get_seller_account(user)
    return account.seller if account else None


def is_seller_owner(user, seller=None):
    account = get_seller_account(user)

    if not account:
        return False

    seller = seller or account.seller

    if not seller or account.seller_id != seller.id:
        return False

    return bool(
        account.is_seller_owner
        or seller.portal_user_id == user.id
    )


def user_has_portal_permission(user, permission_key):
    """
    The owner always has full access.

    Dashboard access is kept available for every valid seller user so login
    always has a safe landing page.
    """
    account = get_seller_account(user)

    if not account:
        return False

    if is_seller_owner(user, account.seller):
        return True

    if permission_key == "dashboard.view":
        return True

    role = account.seller_role

    if (
        not role
        or role.seller_id != account.seller_id
        or not role.is_active
    ):
        return False

    return bool((role.permissions or {}).get(permission_key, False))


def portal_permission_required(permission_key):
    """
    Protect seller portal views on the backend.

    Hiding a menu item is not enough; this decorator prevents direct URL
    access as well.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            account = get_seller_account(request.user)

            if not account:
                if request.path.startswith("/portal/computer/"):
                    return redirect("portal:computer_login")
                return redirect("portal:login")

            if not user_has_portal_permission(
                request.user,
                permission_key,
            ):
                return render(
                    request,
                    "customerportal/computer/permission_denied.html",
                    {
                        "seller": account.seller,
                        "permission_key": permission_key,
                    },
                    status=403,
                )

            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def seed_default_seller_roles(seller):
    """
    Create the three recommended roles once for a seller.

    Existing roles are never overwritten, so customer changes remain safe.
    """
    created_roles = []

    for template in DEFAULT_ROLE_TEMPLATES:
        role, created = SellerPortalRole.objects.get_or_create(
            seller=seller,
            name=template["name"],
            defaults={
                "description": template["description"],
                "permissions": template["permissions"],
                "is_recommended": True,
                "is_active": True,
            },
        )

        if created:
            created_roles.append(role)

    return created_roles


def permissions_from_post(post_data):
    """
    Build a clean permission dictionary from submitted checkboxes.
    Unknown keys are discarded.
    """
    permissions = {
        key: post_data.get(key) == "1"
        for key in ALL_PERMISSION_KEYS
    }

    # Keep a safe login landing page for all seller users.
    permissions["dashboard.view"] = True

    return permissions


def active_additional_user_count(seller):
    """
    Count active, non-owner, non-archived seller users.

    The main owner is not included in the seller quota.
    """
    return (
        Account.objects
        .filter(
            seller=seller,
            account_type=Account.ACCOUNT_TYPE_SELLER,
            is_seller_owner=False,
            is_archived=False,
            user__is_active=True,
        )
        .count()
    )


def available_user_slots(seller):
    limit_value = max(int(seller.max_portal_users or 0), 0)
    used_value = active_additional_user_count(seller)
    return max(limit_value - used_value, 0)


def get_client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")

    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR", "") or ""


def log_portal_action(
    request,
    seller,
    action,
    *,
    target_user=None,
    description="",
    old_value=None,
    new_value=None,
):
    SellerPortalAuditLog.objects.create(
        seller=seller,
        performed_by=(
            request.user
            if request.user.is_authenticated
            else None
        ),
        target_user=target_user,
        action=action,
        description=description,
        old_value=old_value or {},
        new_value=new_value or {},
        ip_address=get_client_ip(request),
    )


def current_role_name(user):
    account = get_seller_account(user)

    if not account:
        return ""

    if is_seller_owner(user, account.seller):
        return "Shop Owner"

    if account.seller_role_id:
        return account.seller_role.name

    return "No Role"
