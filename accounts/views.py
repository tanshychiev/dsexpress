from collections import OrderedDict

from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import Group, Permission, User
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UserCreateForm, UserEditForm, ChangePasswordForm


PER_PAGE = 25


class StaffLoginForm(forms.Form):
    username = forms.CharField()
    password = forms.CharField(widget=forms.PasswordInput)


def staff_login(request):
    if request.user.is_authenticated:
        if request.user.is_staff:
            next_url = request.GET.get("next") or "/"
            return redirect(next_url)
        logout(request)

    form = StaffLoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]

        user = authenticate(request, username=username, password=password)

        if user is None:
            form.add_error(None, "Invalid username or password.")
        elif not user.is_staff:
            form.add_error(None, "Seller accounts cannot access internal system.")
        else:
            login(request, user)
            next_url = request.GET.get("next") or "/"
            return redirect(next_url)

    return render(request, "registration/login.html", {"form": form})


def staff_logout(request):
    logout(request)
    return redirect("/accounts/login/")


# =========================
# USER VIEWS
# =========================
@login_required
@permission_required("auth.view_user", raise_exception=True)
def user_list(request):
    if not request.user.is_staff:
        return redirect("portal:login")

    q = (request.GET.get("q") or "").strip()
    qs = User.objects.all().order_by("-id")

    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )

    paginator = Paginator(qs, PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "accounts/user_list.html",
        {
            "page_obj": page_obj,
            "q": q,
        },
    )


@login_required
@permission_required("auth.add_user", raise_exception=True)
def user_create(request):
    if not request.user.is_staff:
        return redirect("portal:login")

    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"✅ Created user: {user.username}")
            return redirect("user_list")
        messages.error(request, "❌ Please fix errors below.")
    else:
        form = UserCreateForm()

    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "mode": "create",
        },
    )


@login_required
@permission_required("auth.change_user", raise_exception=True)
def user_edit(request, user_id: int):
    if not request.user.is_staff:
        return redirect("portal:login")

    u = get_object_or_404(User, id=user_id)

    if request.method == "POST":
        form = UserEditForm(request.POST, instance=u)
        if form.is_valid():
            form.save()
            messages.success(request, f"✅ Updated user: {u.username}")
            return redirect("user_list")
        messages.error(request, "❌ Please fix errors below.")
    else:
        form = UserEditForm(instance=u)

    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "mode": "edit",
            "u": u,
        },
    )


@login_required
@permission_required("auth.change_user", raise_exception=True)
def user_change_password(request, user_id: int):
    if not request.user.is_staff:
        return redirect("portal:login")

    u = get_object_or_404(User, id=user_id)

    if request.method == "POST":
        form = ChangePasswordForm(request.POST)
        if form.is_valid():
            u.set_password(form.cleaned_data["password1"])
            u.save()
            messages.success(request, f"✅ Password changed for: {u.username}")
            return redirect("user_edit", user_id=u.id)
        messages.error(request, "❌ Please fix errors below.")
    else:
        form = ChangePasswordForm()

    return render(
        request,
        "accounts/user_password.html",
        {
            "form": form,
            "u": u,
        },
    )


@login_required
@permission_required("auth.delete_user", raise_exception=True)
def user_delete(request, user_id: int):
    if not request.user.is_staff:
        return redirect("portal:login")

    u = get_object_or_404(User, id=user_id)

    if request.method == "POST":
        if u.is_superuser:
            messages.error(request, "❌ You cannot delete a superuser.")
            return redirect("user_list")

        u.delete()
        messages.success(request, f"✅ Deleted user: {u.username}")
        return redirect("user_list")

    return render(
        request,
        "accounts/user_delete.html",
        {
            "u": u,
        },
    )


# =========================
# ROLE VIEWS (Django Group)
# =========================
@login_required
@permission_required("auth.view_group", raise_exception=True)
def role_list(request):
    if not request.user.is_staff:
        return redirect("portal:login")

    q = (request.GET.get("q") or "").strip()
    qs = Group.objects.all().order_by("name")

    if q:
        qs = qs.filter(name__icontains=q)

    paginator = Paginator(qs, PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "accounts/role_list.html",
        {
            "page_obj": page_obj,
            "q": q,
        },
    )


def _permission_map():
    perm_by_app = OrderedDict()
    perms = Permission.objects.select_related("content_type").order_by(
        "content_type__app_label",
        "codename",
    )

    for p in perms:
        app = p.content_type.app_label
        perm_by_app.setdefault(app, [])
        perm_by_app[app].append(p)

    return perm_by_app


@login_required
@permission_required("auth.add_group", raise_exception=True)
def role_create(request):
    if not request.user.is_staff:
        return redirect("portal:login")

    perm_by_app = _permission_map()

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        selected_ids = request.POST.getlist("permissions")

        if not name:
            messages.error(request, "❌ Role name is required.")
            return render(
                request,
                "accounts/role_form.html",
                {
                    "mode": "create",
                    "role": None,
                    "perm_by_app": perm_by_app,
                    "selected_ids": {int(x) for x in selected_ids if str(x).isdigit()},
                },
            )

        if Group.objects.filter(name__iexact=name).exists():
            messages.error(request, "❌ Role name already exists.")
            return render(
                request,
                "accounts/role_form.html",
                {
                    "mode": "create",
                    "role": None,
                    "perm_by_app": perm_by_app,
                    "selected_ids": {int(x) for x in selected_ids if str(x).isdigit()},
                },
            )

        role = Group.objects.create(name=name)

        if selected_ids:
            role.permissions.set(Permission.objects.filter(id__in=selected_ids))

        messages.success(request, f"✅ Created role: {role.name}")
        return redirect("role_list")

    return render(
        request,
        "accounts/role_form.html",
        {
            "mode": "create",
            "role": None,
            "perm_by_app": perm_by_app,
            "selected_ids": set(),
        },
    )
@login_required
@permission_required("auth.change_group", raise_exception=True)
def role_edit(request, role_id: int):
    if not request.user.is_staff:
        return redirect("portal:login")

    role = get_object_or_404(Group, id=role_id)
    perm_by_app = _permission_map()

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        selected_ids = request.POST.getlist("permissions")

        if not name:
            messages.error(request, "❌ Role name is required.")
            return render(
                request,
                "accounts/role_form.html",
                {
                    "mode": "edit",
                    "role": role,
                    "perm_by_app": perm_by_app,
                    "selected_ids": {int(x) for x in selected_ids if str(x).isdigit()},
                },
            )

        if Group.objects.exclude(id=role.id).filter(name__iexact=name).exists():
            messages.error(request, "❌ Another role already uses this name.")
            return render(
                request,
                "accounts/role_form.html",
                {
                    "mode": "edit",
                    "role": role,
                    "perm_by_app": perm_by_app,
                    "selected_ids": {int(x) for x in selected_ids if str(x).isdigit()},
                },
            )

        role.name = name
        role.save()
        role.permissions.set(Permission.objects.filter(id__in=selected_ids))

        messages.success(request, f"✅ Updated role: {role.name}")
        return redirect("role_list")

    return render(
        request,
        "accounts/role_form.html",
        {
            "mode": "edit",
            "role": role,
            "perm_by_app": perm_by_app,
            "selected_ids": set(role.permissions.values_list("id", flat=True)),
        },
    )


@login_required
@permission_required("auth.delete_group", raise_exception=True)
def role_delete(request, role_id: int):
    if not request.user.is_staff:
        return redirect("portal:login")

    role = get_object_or_404(Group, id=role_id)

    if request.method == "POST":
        if role.name.lower() == "admin":
            messages.error(request, "❌ You cannot delete Admin role.")
            return redirect("role_list")

        role.delete()
        messages.success(request, f"✅ Deleted role: {role.name}")
        return redirect("role_list")

    return render(
        request,
        "accounts/role_delete.html",
        {
            "role": role,
        },
    )