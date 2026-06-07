from __future__ import annotations

import json
from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from masterdata.models import Seller
from orders.models import Order

from .forms import InventorySellerSettingForm
from .models import OrderStockItem, StockMovement, StockProduct
from .services import (
    add_stock_in,
    adjust_stock,
    confirm_stock as confirm_stock_service,
    current_available_qty,
    get_seller_inventory_setting,
    last_confirmed,
    reserved_qty,
    save_alias_from_order_link,
    set_order_stock,
    set_order_stock_items,
)


def staff_only(request):
    return request.user.is_authenticated and request.user.is_staff


def seller_display(seller):
    if not seller:
        return ""
    code = getattr(seller, "code", "") or ""
    name = getattr(seller, "name", "") or ""
    return f"{name} - {code}".strip(" -")


def product_display(product):
    if not product:
        return ""
    sku = getattr(product, "sku", "") or ""
    name = getattr(product, "name", "") or ""
    return f"{name} - {sku}".strip(" -")


def get_selected_seller_from_request(request):
    seller_id = (
        request.POST.get("seller_id")
        or request.POST.get("seller")
        or request.GET.get("seller_id")
        or request.GET.get("seller")
        or ""
    ).strip()

    if seller_id.isdigit():
        return Seller.objects.filter(id=int(seller_id), is_active=True).first()

    return None


def get_selected_product_from_request(request):
    product_id = (
        request.POST.get("product_id")
        or request.POST.get("product")
        or request.GET.get("product_id")
        or request.GET.get("product")
        or ""
    ).strip()

    if product_id.isdigit():
        return StockProduct.objects.filter(id=int(product_id), is_active=True).first()

    return None


def inventory_date_range(request):
    today = timezone.localdate()

    from_date_raw = (request.GET.get("from_date") or today.isoformat()).strip()
    to_date_raw = (request.GET.get("to_date") or today.isoformat()).strip()

    try:
        from_date = datetime.strptime(from_date_raw, "%Y-%m-%d").date()
    except Exception:
        from_date = today

    try:
        to_date = datetime.strptime(to_date_raw, "%Y-%m-%d").date()
    except Exception:
        to_date = today

    start_dt = timezone.make_aware(datetime.combine(from_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(to_date, time.max))

    return from_date, to_date, start_dt, end_dt


@login_required
def inventory_list(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    seller_id = (request.GET.get("seller_id") or "").strip()
    q = (request.GET.get("q") or "").strip()

    sellers = Seller.objects.filter(is_active=True).order_by("name")

    selected_seller = None
    selected_seller_display = ""

    if seller_id.isdigit():
        selected_seller = Seller.objects.filter(id=int(seller_id), is_active=True).first()
        selected_seller_display = seller_display(selected_seller)

    products = (
        StockProduct.objects
        .select_related("seller")
        .filter(is_active=True)
        .order_by("seller__name", "name")
    )

    if selected_seller:
        products = products.filter(seller=selected_seller)

    if q:
        products = products.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(product_type__icontains=q)
            | Q(location__icontains=q)
            | Q(seller__name__icontains=q)
            | Q(seller__code__icontains=q)
        )

    rows = []

    for product in products:
        available = current_available_qty(product)
        reserved = reserved_qty(product)
        snapshot = last_confirmed(product)
        setting = get_seller_inventory_setting(product.seller)

        if setting.stock_mode == "STRICT":
            stock_mode_label = "Strict Stock"
        elif setting.stock_mode == "NO_STOCK":
            stock_mode_label = "No Stock"
        else:
            stock_mode_label = "Optional Stock"

        rows.append({
            "product": product,
            "product_id": product.id,
            "photo_url": product.photo.url if product.photo else "",
            "name": product.name,
            "sku": product.sku,
            "product_type": product.product_type,
            "location": product.location,
            "current_qty": available + reserved,
            "reserved_qty": reserved,
            "available_qty": available,
            "last_confirmed_at": snapshot.confirmed_at if snapshot else None,
            "stock_mode": setting.stock_mode,
            "stock_mode_label": stock_mode_label,
            "show_stock_in_portal": setting.show_stock_in_portal,
        })

    return render(
        request,
        "inventory/list.html",
        {
            "rows": rows,
            "sellers": sellers,
            "selected_seller_id": seller_id,
            "selected_seller_display": selected_seller_display,
            "q": q,
        },
    )


@login_required
def stock_in(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    selected_seller = get_selected_seller_from_request(request)
    selected_product = get_selected_product_from_request(request)

    if request.method == "POST":
        seller = selected_seller
        product = selected_product

        new_product_name = (request.POST.get("new_product_name") or "").strip()
        product_type = (request.POST.get("product_type") or "").strip()
        location = (request.POST.get("location") or "").strip()
        qty_raw = (request.POST.get("qty") or "").strip()
        note = (request.POST.get("note") or "").strip()
        photo = request.FILES.get("photo")

        try:
            qty = int(qty_raw or 0)
        except Exception:
            qty = 0

        if not seller:
            messages.error(request, "Please choose seller/shop.")
            return redirect("inventory:stock_in")

        if qty <= 0:
            messages.error(request, "Qty must be greater than 0.")
            return redirect("inventory:stock_in")

        with transaction.atomic():
            if not product:
                if not new_product_name:
                    messages.error(request, "Choose existing product or enter new product name.")
                    return redirect("inventory:stock_in")

                product = StockProduct.objects.create(
                    seller=seller,
                    name=new_product_name,
                    product_type=product_type,
                    location=location,
                    photo=photo,
                    created_by=request.user,
                )
            else:
                changed = False

                if photo:
                    product.photo = photo
                    changed = True

                if product_type:
                    product.product_type = product_type
                    changed = True

                if location:
                    product.location = location
                    changed = True

                if changed:
                    product.save()

            add_stock_in(
                product=product,
                qty=qty,
                actor=request.user,
                note=note or "Stock in",
            )

        messages.success(request, f"Stock in saved: {product.name} +{qty}")
        return redirect("inventory:list")

    return render(
        request,
        "inventory/stock_in.html",
        {
            "selected_seller_id": selected_seller.id if selected_seller else "",
            "selected_seller_display": seller_display(selected_seller),
            "selected_product_id": selected_product.id if selected_product else "",
            "selected_product_display": product_display(selected_product),
        },
    )


@login_required
def adjust_stock_view(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    selected_seller = get_selected_seller_from_request(request)
    selected_product = get_selected_product_from_request(request)

    if request.method == "POST":
        product = selected_product
        real_qty_raw = (request.POST.get("real_qty") or "").strip()
        diff_qty_raw = (request.POST.get("diff_qty") or "").strip()
        note = (request.POST.get("note") or "").strip()

        if not product:
            messages.error(request, "Please choose product.")
            return redirect("inventory:adjust")

        real_qty = None
        diff_qty = None

        try:
            if real_qty_raw != "":
                real_qty = int(real_qty_raw)
        except Exception:
            messages.error(request, "Real qty must be a number.")
            return redirect("inventory:adjust")

        try:
            if diff_qty_raw != "":
                diff_qty = int(diff_qty_raw)
        except Exception:
            messages.error(request, "Adjustment qty must be a number.")
            return redirect("inventory:adjust")

        if real_qty is None and diff_qty is None:
            messages.error(request, "Enter real qty or adjustment qty.")
            return redirect("inventory:adjust")

        adjust_stock(
            product=product,
            real_qty=real_qty,
            diff_qty=diff_qty,
            actor=request.user,
            note=note or "Stock adjustment",
        )

        messages.success(request, "Stock adjusted.")
        return redirect("inventory:list")

    return render(
        request,
        "inventory/adjust_stock.html",
        {
            "selected_seller_id": selected_seller.id if selected_seller else "",
            "selected_seller_display": seller_display(selected_seller),
            "selected_product_id": selected_product.id if selected_product else "",
            "selected_product_display": product_display(selected_product),
        },
    )


@login_required
def confirm_stock_view(request):
    """
    Confirm stock by shop.

    New workflow:
    - choose seller/shop once
    - show all active products for that shop
    - staff can confirm one product row or confirm all rows
    - if real qty differs from system available qty, service auto creates adjustment
    """
    if not staff_only(request):
        return redirect("portal:dashboard")

    selected_seller = get_selected_seller_from_request(request)

    if request.method == "POST":
        if not selected_seller:
            messages.error(request, "Please choose seller/shop first.")
            return redirect("inventory:confirm")

        only_product_id = (request.POST.get("only_product_id") or "").strip()
        product_ids = request.POST.getlist("product_id")

        # If user clicked one row Confirm button, only confirm that row.
        if only_product_id.isdigit():
            product_ids = [only_product_id]

        confirmed_count = 0
        adjusted_count = 0
        skipped_count = 0

        with transaction.atomic():
            for product_id in product_ids:
                if not str(product_id).isdigit():
                    skipped_count += 1
                    continue

                product = (
                    StockProduct.objects
                    .filter(
                        id=int(product_id),
                        seller=selected_seller,
                        is_active=True,
                    )
                    .first()
                )

                if not product:
                    skipped_count += 1
                    continue

                real_qty_raw = (
                    request.POST.get(f"real_qty_{product.id}")
                    or request.POST.get("real_qty")
                    or ""
                ).strip()

                note = (
                    request.POST.get(f"note_{product.id}")
                    or request.POST.get("note")
                    or ""
                ).strip()

                if real_qty_raw == "":
                    skipped_count += 1
                    continue

                try:
                    real_qty = int(real_qty_raw)
                except Exception:
                    skipped_count += 1
                    continue

                if real_qty < 0:
                    skipped_count += 1
                    continue

                before_qty = current_available_qty(product)

                confirm_stock_service(
                    product=product,
                    real_qty=real_qty,
                    actor=request.user,
                    note=(
                        note
                        or (
                            "Confirm stock by shop page. "
                            f"System available before: {before_qty}, real count: {real_qty}"
                        )
                    ),
                )

                confirmed_count += 1

                if before_qty != real_qty:
                    adjusted_count += 1

        if confirmed_count:
            messages.success(
                request,
                (
                    f"✅ Confirmed {confirmed_count} product(s). "
                    f"Adjusted {adjusted_count}. Skipped {skipped_count}."
                ),
            )
        else:
            messages.error(request, "No product was confirmed. Please enter real qty.")

        return redirect(f"{request.path}?seller_id={selected_seller.id}")

    products = []

    if selected_seller:
        qs = (
            StockProduct.objects
            .filter(seller=selected_seller, is_active=True)
            .order_by("name", "sku")
        )

        for product in qs:
            available = current_available_qty(product)
            reserved = reserved_qty(product)
            snapshot = last_confirmed(product)

            products.append({
                "product": product,
                "product_id": product.id,
                "photo_url": product.photo.url if product.photo else "",
                "name": product.name,
                "sku": product.sku,
                "product_type": product.product_type,
                "location": product.location,
                "current_qty": available + reserved,
                "reserved_qty": reserved,
                "available_qty": available,
                "last_confirmed_at": snapshot.confirmed_at if snapshot else None,
            })

    return render(
        request,
        "inventory/confirm_stock.html",
        {
            "selected_seller": selected_seller,
            "selected_seller_id": selected_seller.id if selected_seller else "",
            "selected_seller_display": seller_display(selected_seller),
            "selected_product_id": "",
            "selected_product_display": "",
            "products": products,
            "today": timezone.localdate(),
        },
    )

@login_required
def history(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    from_date, to_date, start_dt, end_dt = inventory_date_range(request)

    qs = (
        StockMovement.objects
        .select_related("seller", "product", "order", "created_by")
        .filter(created_at__gte=start_dt, created_at__lte=end_dt)
        .order_by("-created_at", "-id")
    )

    seller_id = (request.GET.get("seller_id") or "").strip()
    product_id = (request.GET.get("product_id") or "").strip()
    tracking = (request.GET.get("tracking") or "").strip()
    q = (request.GET.get("q") or "").strip()

    selected_seller = None

    if seller_id.isdigit():
        selected_seller = Seller.objects.filter(id=int(seller_id), is_active=True).first()
        if selected_seller:
            qs = qs.filter(seller=selected_seller)

    if product_id.isdigit():
        qs = qs.filter(product_id=int(product_id))

    if tracking:
        qs = qs.filter(order__tracking_no__icontains=tracking)

    if q:
        qs = qs.filter(
            Q(seller__name__icontains=q)
            | Q(seller__code__icontains=q)
            | Q(product__name__icontains=q)
            | Q(product__sku__icontains=q)
            | Q(product__location__icontains=q)
            | Q(order__tracking_no__icontains=q)
            | Q(note__icontains=q)
        )

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "inventory/history.html",
        {
            "page_obj": page_obj,
            "q": q,
            "tracking": tracking,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "selected_seller_id": selected_seller.id if selected_seller else "",
            "selected_seller_display": seller_display(selected_seller),
        },
    )


@login_required
def seller_inventory_setting(request, seller_id: int):
    if not staff_only(request):
        return redirect("portal:dashboard")

    seller = get_object_or_404(Seller, id=seller_id)
    setting = get_seller_inventory_setting(seller)

    if request.method == "POST":
        form = InventorySellerSettingForm(request.POST, instance=setting)

        if form.is_valid():
            form.save()
            messages.success(request, "Seller stock setting saved.")
            return redirect("inventory:list")
    else:
        form = InventorySellerSettingForm(instance=setting)

    return render(
        request,
        "inventory/seller_setting.html",
        {
            "seller": seller,
            "form": form,
        },
    )


@login_required
def stock_products_api(request):
    if not staff_only(request):
        return JsonResponse({"results": []}, status=403)

    seller_id = (request.GET.get("seller_id") or "").strip()
    q = (request.GET.get("q") or "").strip()

    qs = (
        StockProduct.objects
        .filter(is_active=True)
        .select_related("seller")
        .order_by("seller__name", "name")
    )

    if seller_id.isdigit():
        qs = qs.filter(seller_id=int(seller_id))

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(product_type__icontains=q)
            | Q(location__icontains=q)
            | Q(seller__name__icontains=q)
            | Q(seller__code__icontains=q)
        )

    results = []

    for product in qs[:80]:
        results.append({
            "id": product.id,
            "seller_id": product.seller_id,
            "seller_name": product.seller.name,
            "seller_code": product.seller.code or "",
            "name": product.name,
            "sku": product.sku or "",
            "product_type": product.product_type or "",
            "location": product.location or "",
            "photo_url": product.photo.url if product.photo else "",
            "available_qty": current_available_qty(product),
        })

    return JsonResponse({"results": results})


@login_required
def choose_order_stock(request, order_id: int):
    if not staff_only(request):
        return redirect("portal:dashboard")

    order = get_object_or_404(Order, id=order_id, is_deleted=False)

    products = (
        StockProduct.objects
        .filter(seller=order.seller, is_active=True)
        .order_by("name")
    )

    product_cards = []

    for product in products:
        product_cards.append({
            "product": product,
            "available_qty": current_available_qty(product),
        })

    existing_items = []

    for item in (
        OrderStockItem.objects
        .select_related("product")
        .filter(order=order)
        .order_by("id")
    ):
        existing_items.append({
            "product_id": item.product_id,
            "name": item.product.name,
            "qty": item.quantity,
        })

    if request.method == "POST":
        stock_items_json = (request.POST.get("stock_items_json") or "").strip()

        if stock_items_json:
            try:
                items_data = json.loads(stock_items_json)
            except Exception:
                items_data = []

            if isinstance(items_data, list) and items_data:
                set_order_stock_items(
                    order=order,
                    items_data=items_data,
                    actor=request.user,
                )

                messages.success(request, "Mixed order stock updated.")

                try:
                    return redirect("order_created", pk=order.id)
                except Exception:
                    return redirect("/orders/")

        product_id = (request.POST.get("product_id") or "").strip()
        qty_raw = (request.POST.get("quantity") or "").strip()
        raw_text = (
            (request.POST.get("raw_product_text") or "").strip()
            or order.product_desc
            or ""
        )

        product = None

        if product_id.isdigit():
            product = StockProduct.objects.filter(
                id=int(product_id),
                seller=order.seller,
                is_active=True,
            ).first()

        try:
            qty = max(int(qty_raw or order.quantity or 1), 1)
        except Exception:
            qty = int(order.quantity or 1)

        link = set_order_stock(
            order=order,
            product=product,
            qty=qty,
            raw_text=raw_text,
            actor=request.user,
            note="Staff chose stock product",
        )

        if product:
            save_alias_from_order_link(link, actor=request.user)

        messages.success(request, "Order stock updated.")

        try:
            return redirect("order_created", pk=order.id)
        except Exception:
            return redirect("/orders/")

    return render(
        request,
        "inventory/choose_order_stock.html",
        {
            "order": order,
            "product_cards": product_cards,
            "current_link": getattr(order, "stock_link", None),
            "existing_items_json": json.dumps(existing_items),
        },
    )


@login_required
def product_edit(request, product_id: int):
    if not staff_only(request):
        return redirect("portal:dashboard")

    product = get_object_or_404(
        StockProduct.objects.select_related("seller"),
        id=product_id,
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "save").strip()

        if action == "delete":
            product.is_active = False
            product.save(update_fields=["is_active"])

            StockMovement.objects.create(
                seller=product.seller,
                product=product,
                movement_type=StockMovement.PRODUCT_CHANGED,
                qty_delta=0,
                created_by=request.user,
                note=f"Product removed/hidden: {product.name}",
            )

            messages.success(request, "Product removed from inventory list.")
            return redirect("inventory:list")

        name = (request.POST.get("name") or "").strip()
        sku = (request.POST.get("sku") or "").strip()
        product_type = (request.POST.get("product_type") or "").strip()
        location = (request.POST.get("location") or "").strip()
        is_active = request.POST.get("is_active") == "on"
        remove_photo = request.POST.get("remove_photo") == "on"
        photo = request.FILES.get("photo")

        if not name:
            messages.error(request, "Product name is required.")
            return redirect("inventory:product_edit", product_id=product.id)

        if sku:
            duplicate_sku = (
                StockProduct.objects
                .filter(seller=product.seller, sku__iexact=sku)
                .exclude(id=product.id)
                .first()
            )

            if duplicate_sku:
                messages.error(
                    request,
                    f"This code/SKU is already used by {duplicate_sku.name}.",
                )
                return redirect("inventory:product_edit", product_id=product.id)

        old_name = product.name
        old_sku = product.sku
        old_type = product.product_type
        old_location = product.location
        old_active = product.is_active
        old_photo = bool(product.photo)

        product.name = name
        product.sku = sku
        product.product_type = product_type
        product.location = location
        product.is_active = is_active

        if remove_photo and product.photo:
            product.photo.delete(save=False)
            product.photo = None

        if photo:
            if product.photo:
                product.photo.delete(save=False)
            product.photo = photo

        product.save()

        StockMovement.objects.create(
            seller=product.seller,
            product=product,
            movement_type=StockMovement.PRODUCT_CHANGED,
            qty_delta=0,
            created_by=request.user,
            note=(
                "Product edited. "
                f"Name: {old_name} -> {product.name}. "
                f"Code: {old_sku or '-'} -> {product.sku or '-'}. "
                f"Type: {old_type or '-'} -> {product.product_type or '-'}. "
                f"Location: {old_location or '-'} -> {product.location or '-'}. "
                f"Active: {old_active} -> {product.is_active}. "
                f"Had photo: {old_photo}, now photo: {bool(product.photo)}."
            ),
        )

        messages.success(request, "Inventory product updated.")
        return redirect("inventory:list")

    return render(
        request,
        "inventory/product_edit.html",
        {
            "product": product,
        },
    )


@login_required
def customer_stock_png(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    seller_id = (request.GET.get("seller_id") or "").strip()
    q = (request.GET.get("q") or "").strip()

    selected_seller = None
    selected_seller_display = ""

    if seller_id.isdigit():
        selected_seller = Seller.objects.filter(id=int(seller_id), is_active=True).first()
        selected_seller_display = seller_display(selected_seller)

    products = (
        StockProduct.objects
        .select_related("seller")
        .filter(is_active=True)
        .order_by("seller__name", "name")
    )

    if selected_seller:
        products = products.filter(seller=selected_seller)

    if q:
        products = products.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(product_type__icontains=q)
            | Q(location__icontains=q)
            | Q(seller__name__icontains=q)
            | Q(seller__code__icontains=q)
        )

    rows = []

    total_current = 0
    total_reserved = 0
    total_available = 0

    for product in products:
        available = current_available_qty(product)
        reserved = reserved_qty(product)
        current = available + reserved
        snapshot = last_confirmed(product)

        total_current += current
        total_reserved += reserved
        total_available += available

        rows.append({
            "product": product,
            "photo_url": product.photo.url if product.photo else "",
            "name": product.name,
            "sku": product.sku,
            "product_type": product.product_type,
            "location": product.location,
            "current_qty": current,
            "reserved_qty": reserved,
            "available_qty": available,
            "last_confirmed_at": snapshot.confirmed_at if snapshot else None,
        })

    return render(
        request,
        "inventory/customer_stock_png.html",
        {
            "rows": rows,
            "selected_seller": selected_seller,
            "selected_seller_display": selected_seller_display,
            "shop_name": selected_seller.name if selected_seller else "All Shops",
            "shop_code": selected_seller.code if selected_seller else "",
            "q": q,
            "server_now": timezone.localtime(),
            "total_current": total_current,
            "total_reserved": total_reserved,
            "total_available": total_available,
        },
    )