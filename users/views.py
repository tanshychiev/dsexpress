from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group, Permission, User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render


# ============================================================
# Helpers
# ============================================================
def _is_admin(user):
    return user.is_authenticated and (user.is_superuser or user.is_staff)


def _permission_groups():
    perms = (
        Permission.objects.select_related("content_type")
        .all()
        .order_by("content_type__app_label", "content_type__model", "codename")
    )
    grouped = defaultdict(list)
    for p in perms:
        grouped[p.content_type.app_label].append(p)
    return dict(sorted(grouped.items(), key=lambda x: x[0].lower()))


def _get_role_id(user: User) -> str:
    g = user.groups.order_by("name").first()
    return str(g.id) if g else ""


def _set_single_role(user: User, role_id: str):
    user.groups.clear()
    if role_id and str(role_id).isdigit():
        role = Group.objects.filter(id=int(role_id)).first()
        if role:
            user.groups.add(role)


# ============================================================
# ROLES (Groups)
# ============================================================
@login_required
@user_passes_test(_is_admin)
def role_list(request):
    q = (request.GET.get("q") or "").strip()
    roles = Group.objects.all().order_by("name")
    if q:
        roles = roles.filter(name__icontains=q)

    items = [{"role": r, "perm_count": r.permissions.count()} for r in roles]
    return render(request, "users/role_list.html", {"items": items, "q": q})


@login_required
@user_passes_test(_is_admin)
@transaction.atomic
def role_create(request):
    perm_by_app = _permission_groups()

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        perm_ids = request.POST.getlist("permissions")
        clean_ids = [int(x) for x in perm_ids if str(x).isdigit()]

        if not name:
            messages.error(request, "Role name is required.")
            return render(request, "users/role_form.html", {"mode": "create", "perm_by_app": perm_by_app})

        if Group.objects.filter(name__iexact=name).exists():
            messages.error(request, "Role name already exists.")
            return render(request, "users/role_form.html", {"mode": "create", "perm_by_app": perm_by_app})

        role = Group.objects.create(name=name)
        role.permissions.set(Permission.objects.filter(id__in=clean_ids))

        messages.success(request, f"✅ Role created: {role.name}")
        return redirect("role_list")

    return render(request, "users/role_form.html", {"mode": "create", "perm_by_app": perm_by_app})


@login_required
@user_passes_test(_is_admin)
@transaction.atomic
def role_edit(request, pk: int):
    role = get_object_or_404(Group, pk=pk)
    perm_by_app = _permission_groups()
    selected_ids = set(role.permissions.values_list("id", flat=True))

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        perm_ids = request.POST.getlist("permissions")
        clean_ids = [int(x) for x in perm_ids if str(x).isdigit()]

        if not name:
            messages.error(request, "Role name is required.")
            return render(
                request,
                "users/role_form.html",
                {"mode": "edit", "role": role, "perm_by_app": perm_by_app, "selected_ids": selected_ids},
            )

        if Group.objects.filter(name__iexact=name).exclude(pk=role.pk).exists():
            messages.error(request, "Role name already exists.")
            return render(
                request,
                "users/role_form.html",
                {"mode": "edit", "role": role, "perm_by_app": perm_by_app, "selected_ids": selected_ids},
            )

        role.name = name
        role.save()
        role.permissions.set(Permission.objects.filter(id__in=clean_ids))

        messages.success(request, f"✅ Role updated: {role.name}")
        return redirect("role_list")

    return render(
        request,
        "users/role_form.html",
        {"mode": "edit", "role": role, "perm_by_app": perm_by_app, "selected_ids": selected_ids},
    )


@login_required
@user_passes_test(_is_admin)
@transaction.atomic
def role_delete(request, pk: int):
    role = get_object_or_404(Group, pk=pk)

    if request.method == "POST":
        name = role.name
        role.delete()
        messages.success(request, f"✅ Role deleted: {name}")
        return redirect("role_list")

    return render(request, "users/role_delete.html", {"role": role})


# ============================================================
# USERS
# ============================================================
PER_PAGE = 30


@login_required
@user_passes_test(_is_admin)
def user_list(request):
    q = (request.GET.get("q") or "").strip()

    qs = User.objects.all().order_by("-date_joined")
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )

    paginator = Paginator(qs, PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "users/user_list.html", {"page_obj": page_obj, "q": q})


@login_required
@user_passes_test(_is_admin)
@transaction.atomic
def user_create(request):
    roles = Group.objects.all().order_by("name")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        first_name = (request.POST.get("first_name") or "").strip()
        last_name = (request.POST.get("last_name") or "").strip()

        pw1 = (request.POST.get("password") or "").strip()
        pw2 = (request.POST.get("password2") or "").strip()

        is_active = request.POST.get("is_active") == "1"
        role_id = (request.POST.get("role_id") or "").strip()

        errors = []
        if not username:
            errors.append("Username is required.")
        if User.objects.filter(username__iexact=username).exists():
            errors.append("Username already exists.")
        if pw1 != pw2:
            errors.append("Password not match.")
        if len(pw1) < 4:
            errors.append("Password must be at least 4 characters.")

        if errors:
            return render(
                request,
                "users/user_form.html",
                {
                    "mode": "create",
                    "roles": roles,
                    "selected_role_id": role_id,
                    "errors": errors,
                    "form": {
                        "username": username,
                        "email": email,
                        "first_name": first_name,
                        "last_name": last_name,
                        "is_active": is_active,
                    },
                },
            )

        u = User.objects.create(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=make_password(pw1),
            is_active=is_active,
            is_staff=False,  # we won't use staff now (role controls permissions)
        )
        _set_single_role(u, role_id)

        messages.success(request, "✅ User created.")
        return redirect("user_list")

    return render(request, "users/user_form.html", {"mode": "create", "roles": roles, "selected_role_id": "", "errors": [], "form": {}})


@login_required
@user_passes_test(_is_admin)
@transaction.atomic
def user_edit(request, pk: int):
    u = get_object_or_404(User, pk=pk)
    roles = Group.objects.all().order_by("name")
    selected_role_id = _get_role_id(u)

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        first_name = (request.POST.get("first_name") or "").strip()
        last_name = (request.POST.get("last_name") or "").strip()

        pw1 = (request.POST.get("password") or "").strip()
        pw2 = (request.POST.get("password2") or "").strip()

        is_active = request.POST.get("is_active") == "1"
        role_id = (request.POST.get("role_id") or "").strip()

        errors = []
        if not username:
            errors.append("Username is required.")
        if User.objects.filter(username__iexact=username).exclude(pk=u.pk).exists():
            errors.append("Username already exists.")

        # optional password change
        if pw1 or pw2:
            if pw1 != pw2:
                errors.append("Password not match.")
            elif len(pw1) < 4:
                errors.append("Password must be at least 4 characters.")

        if errors:
            return render(
                request,
                "users/user_form.html",
                {
                    "mode": "edit",
                    "u": u,
                    "roles": roles,
                    "selected_role_id": role_id or selected_role_id,
                    "errors": errors,
                    "form": {
                        "username": username,
                        "email": email,
                        "first_name": first_name,
                        "last_name": last_name,
                        "is_active": is_active,
                    },
                },
            )

        u.username = username
        u.email = email
        u.first_name = first_name
        u.last_name = last_name
        u.is_active = is_active

        if pw1:
            u.set_password(pw1)

        u.save()
        _set_single_role(u, role_id)

        messages.success(request, "✅ User updated.")
        return redirect("user_list")

    return render(
        request,
        "users/user_form.html",
        {
            "mode": "edit",
            "u": u,
            "roles": roles,
            "selected_role_id": selected_role_id,
            "errors": [],
            "form": {
                "username": u.username,
                "email": u.email,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "is_active": u.is_active,
            },
        },
    )


@login_required
@user_passes_test(_is_admin)
@transaction.atomic
def user_password(request, pk: int):
    u = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        pw1 = (request.POST.get("password") or "").strip()
        pw2 = (request.POST.get("password2") or "").strip()

        if pw1 != pw2:
            messages.error(request, "Password not match.")
            return render(request, "users/user_password.html", {"u": u})

        if len(pw1) < 4:
            messages.error(request, "Password must be at least 4 characters.")
            return render(request, "users/user_password.html", {"u": u})

        u.set_password(pw1)
        u.save()

        messages.success(request, "✅ Password updated.")
        return redirect("user_list")

    return render(request, "users/user_password.html", {"u": u})


@login_required
@user_passes_test(_is_admin)
@transaction.atomic
def user_toggle_active(request, pk: int):
    u = get_object_or_404(User, pk=pk)
    u.is_active = not u.is_active
    u.save()
    messages.success(request, f"✅ User {'activated' if u.is_active else 'deactivated'}: {u.username}")
    return redirect("user_list")
@login_required
@user_passes_test(_is_admin)
@transaction.atomic
def user_toggle_active(request, pk: int):
    u = get_object_or_404(User, pk=pk)

    # block superuser
    if u.is_superuser:
        messages.error(request, "❌ You cannot deactivate a superuser/admin.")
        return redirect("user_list")

    u.is_active = not u.is_active
    u.save()

    messages.success(request, f"✅ User {'activated' if u.is_active else 'deactivated'}: {u.username}")
    return redirect("user_list")