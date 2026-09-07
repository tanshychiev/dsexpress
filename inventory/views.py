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

    # Fallback for pages where the visible seller text is filled but the
    # hidden seller_id was not submitted by JavaScript.
    seller_search = (
        request.POST.get("seller_search")
        or request.GET.get("seller_search")
        or ""
    ).strip()

    if seller_search:
        # Common display format is: "Shop Name - CODE"
        if " - " in seller_search:
            name_part, code_part = seller_search.rsplit(" - ", 1)
            code_part = code_part.strip()
            name_part = name_part.strip()

            if code_part:
                seller = Seller.objects.filter(
                    code__iexact=code_part,
                    is_active=True,
                ).first()
                if seller:
                    return seller

            if name_part:
                seller = Seller.objects.filter(
                    name__iexact=name_part,
                    is_active=True,
                ).first()
                if seller:
                    return seller

        # Exact-name fallback.
        seller = Seller.objects.filter(
            name__iexact=seller_search,
            is_active=True,
        ).first()
        if seller:
            return seller

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
    sellers = Seller.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        seller = selected_seller
        note = (request.POST.get("note") or "").strip()
        items_json = (request.POST.get("items_json") or "").strip()

        if not seller:
            messages.error(request, "Please choose seller/shop.")
            return redirect("inventory:stock_in")

        try:
            items_data = json.loads(items_json) if items_json else []
        except Exception:
            items_data = []

        if not isinstance(items_data, list):
            items_data = []

        clean_items = []
        for item_position, raw in enumerate(items_data, start=1):
            if not isinstance(raw, dict):
                continue

            product_id = str(raw.get("product_id") or "").strip()
            new_product_name = str(raw.get("new_product_name") or "").strip()
            product_type = str(raw.get("product_type") or "").strip()
            location = str(raw.get("location") or "").strip()

            # Keep the image/file key sent by the Stock In page.
            # Older versions of the page may only send row_index, so both
            # formats are supported here.
            photo_key = str(
                raw.get("photo_key")
                or raw.get("photo_field")
                or raw.get("file_key")
                or ""
            ).strip()

            row_index = str(
                raw.get("row_index")
                or raw.get("row")
                or raw.get("index")
                or ""
            ).strip()

            try:
                qty = int(raw.get("qty") or 0)
            except Exception:
                qty = 0

            if qty <= 0:
                continue

            product = None
            if product_id.isdigit():
                product = StockProduct.objects.filter(
                    id=int(product_id),
                    seller=seller,
                    is_active=True,
                ).first()

            if not product and not new_product_name:
                continue

            clean_items.append({
                "product": product,
                "new_product_name": new_product_name,
                "product_type": product_type,
                "location": location,
                "photo_key": photo_key,
                "row_index": row_index,
                "item_position": item_position,
                "qty": qty,
            })

        if not clean_items:
            messages.error(request, "Add at least one product with quantity greater than 0.")
            return redirect("inventory:stock_in")

        now = timezone.localtime()
        batch_ref = f"RCV-{now:%Y%m%d-%H%M%S}-{request.user.id}"
        common_note = f"[BATCH:{batch_ref}] {note or 'Batch stock in'}"

        with transaction.atomic():
            for item in clean_items:
                product = item["product"]

                # Resolve the uploaded image robustly. The current Stock In UI
                # can send photo_key, while older UI versions used photo_<row>.
                photo_candidates = []

                if item.get("photo_key"):
                    photo_candidates.append(item["photo_key"])

                if item.get("row_index"):
                    photo_candidates.extend([
                        f"photo_{item['row_index']}",
                        f"image_{item['row_index']}",
                    ])

                # Safe fallbacks for rows generated without row_index.
                photo_candidates.extend([
                    f"photo_{item['item_position']}",
                    f"image_{item['item_position']}",
                ])

                uploaded_photo = None
                for key in photo_candidates:
                    if key and key in request.FILES:
                        uploaded_photo = request.FILES.get(key)
                        if uploaded_photo:
                            break

                if not product:
                    product = StockProduct.objects.create(
                        seller=seller,
                        name=item["new_product_name"],
                        product_type=item["product_type"],
                        location=item["location"],
                        photo=uploaded_photo or None,
                        created_by=request.user,
                    )
                else:
                    changed_fields = []

                    if item["product_type"] and item["product_type"] != product.product_type:
                        product.product_type = item["product_type"]
                        changed_fields.append("product_type")

                    if item["location"] and item["location"] != product.location:
                        product.location = item["location"]
                        changed_fields.append("location")

                    # If a new image was selected during Stock In, save it to
                    # the product so Inventory and the receipt can display it.
                    if uploaded_photo:
                        product.photo = uploaded_photo
                        changed_fields.append("photo")

                    if changed_fields:
                        # Remove duplicates while preserving field order.
                        changed_fields = list(dict.fromkeys(changed_fields))
                        product.save(update_fields=changed_fields)

                add_stock_in(
                    product=product,
                    qty=item["qty"],
                    actor=request.user,
                    note=common_note,
                )

        messages.success(request, f"Batch stock in saved: {len(clean_items)} item(s).")
        return redirect("inventory:stock_in_receipt", batch_ref=batch_ref)

    return render(
        request,
        "inventory/stock_in.html",
        {
            "sellers": sellers,
            "selected_seller_id": selected_seller.id if selected_seller else "",
        },
    )


@login_required
def stock_in_receipt(request, batch_ref: str):
    if not staff_only(request):
        return redirect("portal:dashboard")

    batch_ref = (batch_ref or "").strip()
    marker = f"[BATCH:{batch_ref}]"

    movements = list(
        StockMovement.objects
        .select_related("seller", "product", "created_by")
        .filter(
            movement_type=StockMovement.STOCK_IN,
            note__contains=marker,
        )
        .order_by("id")
    )

    if not movements:
        messages.error(request, "Receive goods receipt not found.")
        return redirect("inventory:list")

    first = movements[0]
    seller = first.seller
    total_qty = sum(max(int(m.qty_delta or 0), 0) for m in movements)
    clean_note = (first.note or "").replace(marker, "", 1).strip()

    return render(
        request,
        "inventory/stock_in_receipt.html",
        {
            "batch_ref": batch_ref,
            "seller": seller,
            "movements": movements,
            "total_qty": total_qty,
            "received_at": first.created_at,
            "received_by": first.created_by,
            "note": clean_note,
        },
    )


@login_required
def stock_in_list(request):
    """List batch Stock In receipts reconstructed from existing StockMovement rows."""
    if not staff_only(request):
        return redirect("portal:dashboard")

    seller_id = (request.GET.get("seller_id") or "").strip()
    q = (request.GET.get("q") or "").strip()
    from_date, to_date, start_dt, end_dt = inventory_date_range(request)

    qs = (
        StockMovement.objects
        .select_related("seller", "product", "created_by")
        .filter(
            movement_type=StockMovement.STOCK_IN,
            created_at__gte=start_dt,
            created_at__lte=end_dt,
            note__contains="[BATCH:",
        )
        .order_by("-created_at", "-id")
    )

    if seller_id.isdigit():
        qs = qs.filter(seller_id=int(seller_id))

    if q:
        qs = qs.filter(
            Q(seller__name__icontains=q)
            | Q(seller__code__icontains=q)
            | Q(product__name__icontains=q)
            | Q(product__sku__icontains=q)
            | Q(note__icontains=q)
        )

    batches = {}
    for movement in qs:
        note = movement.note or ""
        start = note.find("[BATCH:")
        end = note.find("]", start)
        if start < 0 or end < 0:
            continue

        batch_ref = note[start + 7:end].strip()
        if not batch_ref:
            continue

        row = batches.setdefault(
            batch_ref,
            {
                "batch_ref": batch_ref,
                "seller": movement.seller,
                "received_at": movement.created_at,
                "received_by": movement.created_by,
                "total_qty": 0,
                "product_count": 0,
                "products": [],
                "note": note[end + 1:].strip(),
            },
        )

        qty = max(int(movement.qty_delta or 0), 0)
        row["total_qty"] += qty
        row["product_count"] += 1
        row["products"].append(
            {
                "name": movement.product.name if movement.product else "-",
                "sku": movement.product.sku if movement.product else "",
                "qty": qty,
            }
        )

    rows = list(batches.values())
    rows.sort(key=lambda x: x["received_at"], reverse=True)

    paginator = Paginator(rows, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "inventory/stock_in_list.html",
        {
            "page_obj": page_obj,
            "sellers": Seller.objects.filter(is_active=True).order_by("name"),
            "selected_seller_id": seller_id,
            "q": q,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
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
    if not staff_only(request):
        return redirect("portal:dashboard")

    selected_seller = get_selected_seller_from_request(request)
    selected_product = get_selected_product_from_request(request)

    if request.method == "POST":
        product = selected_product
        real_qty_raw = (request.POST.get("real_qty") or "").strip()
        note = (request.POST.get("note") or "").strip()

        if not product:
            messages.error(request, "Please choose product.")
            return redirect("inventory:confirm")

        try:
            real_qty = int(real_qty_raw or 0)
        except Exception:
            messages.error(request, "Real qty must be a number.")
            return redirect("inventory:confirm")

        if real_qty < 0:
            messages.error(request, "Real qty cannot be negative.")
            return redirect("inventory:confirm")

        confirm_stock(
            product=product,
            real_qty=real_qty,
            actor=request.user,
            note=note or "Stock confirmed",
        )

        messages.success(request, f"Stock confirmed: {product.name} = {real_qty}")
        return redirect("inventory:list")

    return render(
        request,
        "inventory/confirm_stock.html",
        {
            "selected_seller_id": selected_seller.id if selected_seller else "",
            "selected_seller_display": seller_display(selected_seller),
            "selected_product_id": selected_product.id if selected_product else "",
            "selected_product_display": product_display(selected_product),
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