from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from masterdata.models import Seller
from orders.models import Order

from .forms import (
    AdjustStockForm,
    ConfirmStockForm,
    InventorySellerSettingForm,
    StockInForm,
)
from .models import OrderStockItem, StockMovement, StockProduct
from .services import (
    add_stock_in,
    adjust_stock,
    confirm_stock,
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

@login_required
def inventory_list(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    seller_id = (request.GET.get("seller_id") or "").strip()
    q = (request.GET.get("q") or "").strip()

    sellers = Seller.objects.filter(is_active=True).order_by("name")

    selected_seller_display = ""
    selected_seller = None

    if seller_id.isdigit():
        selected_seller = Seller.objects.filter(id=int(seller_id), is_active=True).first()
        if selected_seller:
            selected_seller_display = f"{selected_seller.name} - {selected_seller.code or ''}".strip(" -")

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

    seller = None
    seller_id = request.POST.get("seller") or request.GET.get("seller_id")

    if seller_id and str(seller_id).isdigit():
        seller = Seller.objects.filter(id=int(seller_id)).first()

    if request.method == "POST":
        form = StockInForm(request.POST, request.FILES, seller=seller)

        if form.is_valid():
            seller = form.cleaned_data["seller"]
            product = form.cleaned_data.get("product")
            new_product_name = (form.cleaned_data.get("new_product_name") or "").strip()
            product_type = (form.cleaned_data.get("product_type") or "").strip()
            photo = form.cleaned_data.get("photo")
            location = (form.cleaned_data.get("location") or "").strip()
            qty = form.cleaned_data["qty"]
            note = form.cleaned_data.get("note") or ""

            with transaction.atomic():
                if not product:
                    if not new_product_name:
                        form.add_error(
                            "new_product_name",
                            "Choose existing product or enter new product name.",
                        )
                    else:
                        product = StockProduct.objects.create(
                            seller=seller,
                            name=new_product_name,
                            product_type=product_type,
                            photo=photo,
                            location=location,
                            created_by=request.user,
                        )
                else:
                    changed = False

                    if photo:
                        product.photo = photo
                        changed = True

                    if location:
                        product.location = location
                        changed = True

                    if product_type and not product.product_type:
                        product.product_type = product_type
                        changed = True

                    if changed:
                        product.save()

                if product and not form.errors:
                    add_stock_in(
                        product=product,
                        qty=qty,
                        actor=request.user,
                        note=note,
                    )

                    messages.success(
                        request,
                        f"Stock in saved: {product.name} +{qty}",
                    )
                    return redirect("inventory:list")
    else:
        form = StockInForm(seller=seller)

    return render(
        request,
        "inventory/stock_in.html",
        {
            "form": form,
        },
    )


@login_required
def adjust_stock_view(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    if request.method == "POST":
        form = AdjustStockForm(request.POST)

        if form.is_valid():
            product = form.cleaned_data["product"]
            real_qty = form.cleaned_data.get("real_qty")
            diff_qty = form.cleaned_data.get("diff_qty")
            note = form.cleaned_data.get("note") or ""

            adjust_stock(
                product=product,
                real_qty=real_qty,
                diff_qty=diff_qty,
                actor=request.user,
                note=note,
            )

            messages.success(request, "Stock adjusted.")
            return redirect("inventory:list")
    else:
        form = AdjustStockForm()

    return render(
        request,
        "inventory/adjust_stock.html",
        {
            "form": form,
        },
    )


@login_required
def confirm_stock_view(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    if request.method == "POST":
        form = ConfirmStockForm(request.POST)

        if form.is_valid():
            product = form.cleaned_data["product"]
            real_qty = form.cleaned_data["real_qty"]
            note = form.cleaned_data.get("note") or ""

            confirm_stock(
                product=product,
                real_qty=real_qty,
                actor=request.user,
                note=note,
            )

            messages.success(
                request,
                f"Stock confirmed: {product.name} = {real_qty}",
            )
            return redirect("inventory:list")
    else:
        form = ConfirmStockForm()

    return render(
        request,
        "inventory/confirm_stock.html",
        {
            "form": form,
        },
    )


@login_required
def history(request):
    if not staff_only(request):
        return redirect("portal:dashboard")

    qs = (
        StockMovement.objects
        .select_related("seller", "product", "order", "created_by")
        .order_by("-created_at", "-id")
    )

    seller_id = (request.GET.get("seller_id") or "").strip()
    product_id = (request.GET.get("product_id") or "").strip()
    tracking = (request.GET.get("tracking") or "").strip()
    q = (request.GET.get("q") or "").strip()

    if seller_id.isdigit():
        qs = qs.filter(seller_id=int(seller_id))

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
    """
    Used by create order / edit order popup.
    Returns stock products for selected seller.
    """
    if not staff_only(request):
        return JsonResponse({"results": []}, status=403)

    seller_id = (request.GET.get("seller_id") or "").strip()
    q = (request.GET.get("q") or "").strip()

    if not seller_id.isdigit():
        return JsonResponse({"results": []})

    qs = (
        StockProduct.objects
        .filter(seller_id=int(seller_id), is_active=True)
        .select_related("seller")
        .order_by("name")
    )

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(product_type__icontains=q)
            | Q(location__icontains=q)
        )

    results = []

    for product in qs[:80]:
        results.append({
            "id": product.id,
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
    """
    Edit inventory product name / code / SKU / type / location / photo.
    Import can recognize stock by SKU/code or product name.
    """
    if not staff_only(request):
        return redirect("portal:dashboard")

    product = get_object_or_404(
        StockProduct.objects.select_related("seller"),
        id=product_id,
    )

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        sku = (request.POST.get("sku") or "").strip()
        product_type = (request.POST.get("product_type") or "").strip()
        location = (request.POST.get("location") or "").strip()
        is_active = request.POST.get("is_active") == "on"
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

        product.name = name
        product.sku = sku
        product.product_type = product_type
        product.location = location
        product.is_active = is_active

        if photo:
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
                f"Active: {old_active} -> {product.is_active}."
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