from __future__ import annotations

import json
from typing import Optional

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from masterdata.models import Seller

from .models import (
    InventorySellerSetting,
    OrderStockItem,
    OrderStockLink,
    StockAlias,
    StockMovement,
    StockProduct,
    StockSnapshot,
)


def normalize_text(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


def get_seller_inventory_setting(seller: Seller) -> InventorySellerSetting:
    setting, _ = InventorySellerSetting.objects.get_or_create(seller=seller)
    return setting


def current_available_qty(product: StockProduct) -> int:
    """
    Current available stock.

    Formula:
    last confirmed qty
    + all stock movements after last confirm date

    If no confirmed stock yet:
    all movements from beginning.
    """
    last_snapshot = (
        StockSnapshot.objects
        .filter(product=product)
        .order_by("-confirmed_at", "-id")
        .first()
    )

    qs = StockMovement.objects.filter(product=product)
    base_qty = 0

    if last_snapshot:
        base_qty = int(last_snapshot.confirmed_qty or 0)
        qs = qs.filter(created_at__gt=last_snapshot.confirmed_at)

    movement_total = qs.aggregate(total=Sum("qty_delta"))["total"] or 0

    return int(base_qty) + int(movement_total)


def reserved_qty(product: StockProduct) -> int:
    """
    Qty currently reserved by pending orders.
    This is for display only.
    """
    link_reserved = (
        OrderStockLink.objects
        .filter(product=product, reserved_qty__gt=0)
        .aggregate(total=Sum("reserved_qty"))
        .get("total")
        or 0
    )

    item_reserved = (
        OrderStockItem.objects
        .filter(product=product, reserved_qty__gt=0)
        .aggregate(total=Sum("reserved_qty"))
        .get("total")
        or 0
    )

    return int(link_reserved) + int(item_reserved)


def last_confirmed(product: StockProduct):
    return (
        StockSnapshot.objects
        .filter(product=product)
        .order_by("-confirmed_at", "-id")
        .first()
    )


def get_seller_current_stock(seller: Seller):
    """
    Safe data for seller portal.
    Seller can see current stock only, not internal history.
    """
    rows = []

    products = (
        StockProduct.objects
        .filter(seller=seller, is_active=True)
        .order_by("name")
    )

    for product in products:
        available = current_available_qty(product)
        reserved = reserved_qty(product)
        snapshot = last_confirmed(product)

        rows.append(
            {
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
            }
        )

    return rows


def match_product(seller: Seller, raw_text: str | None) -> Optional[StockProduct]:
    """
    Match order product text to stock product.

    Match rule:
    1. SKU exact
    2. Alias exact
    3. Product name exact
    4. Product type exact, only if seller has one product in that type
    5. If seller has only one stock product, choose it
    """
    raw = (raw_text or "").strip()

    if not raw:
        return None

    raw_norm = normalize_text(raw)

    product = (
        StockProduct.objects
        .filter(seller=seller, is_active=True, sku__iexact=raw)
        .first()
    )
    if product:
        return product

    alias = (
        StockAlias.objects
        .select_related("product")
        .filter(seller=seller, alias_text__iexact=raw)
        .first()
    )
    if alias and alias.product and alias.product.is_active:
        return alias.product

    exact_name_products = list(
        StockProduct.objects
        .filter(seller=seller, is_active=True, name__iexact=raw)
        [:2]
    )
    if len(exact_name_products) == 1:
        return exact_name_products[0]

    exact_type_products = list(
        StockProduct.objects
        .filter(seller=seller, is_active=True, product_type__iexact=raw)
        [:2]
    )
    if len(exact_type_products) == 1:
        return exact_type_products[0]

    all_products = list(
        StockProduct.objects
        .filter(seller=seller, is_active=True)
        .order_by("name")
    )

    name_matches = [
        p for p in all_products
        if normalize_text(p.name) == raw_norm
    ]
    if len(name_matches) == 1:
        return name_matches[0]

    type_matches = [
        p for p in all_products
        if normalize_text(p.product_type) == raw_norm
    ]
    if len(type_matches) == 1:
        return type_matches[0]

    if len(all_products) == 1:
        return all_products[0]

    return None


def create_movement(
    *,
    seller: Seller,
    product: StockProduct | None,
    movement_type: str,
    qty_delta: int,
    order=None,
    actor=None,
    note: str = "",
) -> StockMovement:
    return StockMovement.objects.create(
        seller=seller,
        product=product,
        order=order,
        movement_type=movement_type,
        qty_delta=int(qty_delta or 0),
        created_by=actor,
        note=note or "",
    )


def add_stock_in(
    *,
    product: StockProduct,
    qty: int,
    actor=None,
    note: str = "",
) -> StockMovement:
    qty = int(qty or 0)

    if qty <= 0:
        raise ValueError("Stock in quantity must be greater than 0.")

    return create_movement(
        seller=product.seller,
        product=product,
        movement_type=StockMovement.STOCK_IN,
        qty_delta=qty,
        actor=actor,
        note=note or "Stock in",
    )


def adjust_stock(
    *,
    product: StockProduct,
    real_qty: int | None = None,
    diff_qty: int | None = None,
    actor=None,
    note: str = "",
):
    """
    Staff can fix wrong stock.

    Use one of:
    - real_qty = physical count
    - diff_qty = add/minus difference
    """
    if real_qty is None and diff_qty is None:
        raise ValueError("Please enter real qty or diff qty.")

    if diff_qty is None:
        diff_qty = int(real_qty) - current_available_qty(product)

    diff_qty = int(diff_qty or 0)

    if diff_qty == 0:
        return None

    return create_movement(
        seller=product.seller,
        product=product,
        movement_type=StockMovement.ADJUSTMENT,
        qty_delta=diff_qty,
        actor=actor,
        note=note or "Stock adjustment",
    )


def confirm_stock(
    *,
    product: StockProduct,
    real_qty: int,
    actor=None,
    note: str = "",
) -> StockSnapshot:
    """
    Confirm today stock is correct.

    If system stock and real stock are different:
    - create adjustment first
    - then save snapshot
    """
    real_qty = int(real_qty or 0)

    with transaction.atomic():
        current_qty = current_available_qty(product)
        diff = real_qty - current_qty

        if diff != 0:
            adjust_stock(
                product=product,
                diff_qty=diff,
                actor=actor,
                note=note or f"Auto adjustment before stock confirm. Real qty: {real_qty}",
            )

        snapshot = StockSnapshot.objects.create(
            seller=product.seller,
            product=product,
            confirmed_qty=real_qty,
            confirmed_by=actor,
            note=note or "Stock confirmed",
        )

        create_movement(
            seller=product.seller,
            product=product,
            movement_type=StockMovement.CONFIRM,
            qty_delta=0,
            actor=actor,
            note=note or f"Stock confirmed = {real_qty}",
        )

        return snapshot


def _get_or_create_order_link(order) -> OrderStockLink:
    link, _ = OrderStockLink.objects.get_or_create(
        order=order,
        defaults={
            "seller": order.seller,
            "raw_product_text": order.product_desc or "",
            "quantity": int(order.quantity or 1),
        },
    )
    return link


def release_order_stock(order, actor=None, note: str = ""):
    """
    Order cancelled / void / product changed.
    Add reserved stock back for single-product stock link.
    """
    link = getattr(order, "stock_link", None)

    if not link or not link.product or link.reserved_qty <= 0:
        return None

    qty = int(link.reserved_qty or 0)

    movement = create_movement(
        seller=link.seller,
        product=link.product,
        order=order,
        movement_type=StockMovement.ORDER_RELEASED,
        qty_delta=qty,
        actor=actor,
        note=note or f"Released stock from order {order.tracking_no}",
    )

    link.reserved_qty = 0
    link.released_at = timezone.now()
    link.updated_by = actor
    link.save(
        update_fields=[
            "reserved_qty",
            "released_at",
            "updated_by",
            "updated_at",
        ]
    )

    return movement


def release_order_stock_items(order, actor=None, note: str = ""):
    """
    Release all mixed-product stock items for one order.
    """
    items = (
        OrderStockItem.objects
        .select_related("product", "seller")
        .filter(order=order, reserved_qty__gt=0)
    )

    for item in items:
        create_movement(
            seller=item.seller,
            product=item.product,
            order=order,
            movement_type=StockMovement.ORDER_RELEASED,
            qty_delta=item.reserved_qty,
            actor=actor,
            note=note or f"Released mixed stock from order {order.tracking_no}",
        )

        item.reserved_qty = 0
        item.save(update_fields=["reserved_qty"])

    link = getattr(order, "stock_link", None)
    if link:
        link.reserved_qty = 0
        link.released_at = timezone.now()
        link.updated_by = actor
        link.save(
            update_fields=[
                "reserved_qty",
                "released_at",
                "updated_by",
                "updated_at",
            ]
        )


def mark_order_delivered(order, actor=None, note: str = ""):
    """
    Delivered order.

    Stock was already deducted when order was reserved.
    Delivery only clears reserved display.
    """
    link = getattr(order, "stock_link", None)

    if link and link.product and not link.delivered_at:
        create_movement(
            seller=link.seller,
            product=link.product,
            order=order,
            movement_type=StockMovement.ORDER_DELIVERED,
            qty_delta=0,
            actor=actor,
            note=note or f"Order delivered {order.tracking_no}",
        )

        link.reserved_qty = 0
        link.delivered_at = timezone.now()
        link.updated_by = actor
        link.save(
            update_fields=[
                "reserved_qty",
                "delivered_at",
                "updated_by",
                "updated_at",
            ]
        )

    OrderStockItem.objects.filter(order=order).update(reserved_qty=0)


def return_order_stock_good(order, actor=None, note: str = ""):
    """
    Return stock back to available stock.
    """
    link = getattr(order, "stock_link", None)

    if link and link.product and not link.returned_at:
        qty = int(link.reserved_qty or link.quantity or 0)

        if qty > 0:
            create_movement(
                seller=link.seller,
                product=link.product,
                order=order,
                movement_type=StockMovement.RETURN_GOOD,
                qty_delta=qty,
                actor=actor,
                note=note or f"Returned good stock from order {order.tracking_no}",
            )

        link.reserved_qty = 0
        link.returned_at = timezone.now()
        link.updated_by = actor
        link.save(
            update_fields=[
                "reserved_qty",
                "returned_at",
                "updated_by",
                "updated_at",
            ]
        )

    items = (
        OrderStockItem.objects
        .select_related("product", "seller")
        .filter(order=order)
    )

    for item in items:
        create_movement(
            seller=item.seller,
            product=item.product,
            order=order,
            movement_type=StockMovement.RETURN_GOOD,
            qty_delta=item.quantity,
            actor=actor,
            note=f"Returned mixed stock: {item.product.name} x{item.quantity}",
        )
        item.reserved_qty = 0
        item.save(update_fields=["reserved_qty"])


def return_order_stock_damaged(order, actor=None, note: str = ""):
    """
    Return damaged.
    Do not add to available stock.
    """
    link = getattr(order, "stock_link", None)

    if link and link.product and not link.returned_at:
        create_movement(
            seller=link.seller,
            product=link.product,
            order=order,
            movement_type=StockMovement.RETURN_DAMAGED,
            qty_delta=0,
            actor=actor,
            note=note or f"Returned damaged stock from order {order.tracking_no}",
        )

        link.reserved_qty = 0
        link.returned_at = timezone.now()
        link.updated_by = actor
        link.save(
            update_fields=[
                "reserved_qty",
                "returned_at",
                "updated_by",
                "updated_at",
            ]
        )

    OrderStockItem.objects.filter(order=order).update(reserved_qty=0)


def set_order_stock(
    *,
    order,
    product: StockProduct | None,
    qty: int | None = None,
    raw_text: str = "",
    actor=None,
    note: str = "",
) -> OrderStockLink:
    """
    Single-product stock for one order.
    """
    qty = int(qty if qty is not None else (order.quantity or 1))
    qty = max(qty, 1)
    raw_text = raw_text or order.product_desc or ""

    with transaction.atomic():
        release_order_stock(
            order,
            actor=actor,
            note="Stock changed, old stock released",
        )
        release_order_stock_items(
            order,
            actor=actor,
            note="Stock changed, old mixed stock released",
        )
        OrderStockItem.objects.filter(order=order).delete()

        link = _get_or_create_order_link(order)
        link.seller = order.seller
        link.raw_product_text = raw_text
        link.quantity = qty
        link.product = product
        link.shortage_qty = 0
        link.updated_by = actor

        setting = get_seller_inventory_setting(order.seller)

        if setting.stock_mode == InventorySellerSetting.NO_STOCK:
            link.product = None
            link.status = OrderStockLink.NO_STOCK_REQUIRED
            link.reserved_qty = 0
            link.save()
            return link

        if product is None:
            link.product = None
            link.status = OrderStockLink.UNMATCHED
            link.reserved_qty = 0
            link.save()

            create_movement(
                seller=order.seller,
                product=None,
                order=order,
                movement_type=StockMovement.IMPORT_UNMATCHED,
                qty_delta=0,
                actor=actor,
                note=f"Unmatched stock text: {raw_text}",
            )
            return link

        available_before = current_available_qty(product)
        shortage = max(qty - available_before, 0)

        link.status = (
            OrderStockLink.STOCK_LACK
            if shortage > 0
            else OrderStockLink.LINKED
        )
        link.shortage_qty = shortage
        link.reserved_qty = qty
        link.save()

        create_movement(
            seller=order.seller,
            product=product,
            order=order,
            movement_type=(
                StockMovement.STOCK_LACK
                if shortage > 0
                else StockMovement.ORDER_RESERVED
            ),
            qty_delta=-qty,
            actor=actor,
            note=note or f"Reserved stock for order {order.tracking_no}. Available before: {available_before}",
        )

        return link


def set_order_stock_items(order, items_data, actor=None):
    """
    Mixed product stock.

    items_data example:
    [
        {"product_id": 1, "qty": 1},
        {"product_id": 2, "qty": 2}
    ]
    """
    with transaction.atomic():
        release_order_stock(order, actor=actor, note="Changing to mixed stock")
        release_order_stock_items(order, actor=actor, note="Changing mixed stock")
        OrderStockItem.objects.filter(order=order).delete()

        link = _get_or_create_order_link(order)
        link.seller = order.seller
        link.product = None
        link.quantity = int(order.quantity or 1)
        link.shortage_qty = 0
        link.reserved_qty = 0
        link.updated_by = actor

        setting = get_seller_inventory_setting(order.seller)

        if setting.stock_mode == InventorySellerSetting.NO_STOCK:
            link.status = OrderStockLink.NO_STOCK_REQUIRED
            link.save()
            return link

        total_reserved = 0
        total_shortage = 0
        product_desc_parts = []

        for item in items_data:
            product_id = item.get("product_id") or item.get("id")
            qty = int(item.get("qty") or item.get("quantity") or 1)
            qty = max(qty, 1)

            product = StockProduct.objects.filter(
                id=product_id,
                seller=order.seller,
                is_active=True,
            ).first()

            if not product:
                continue

            available_before = current_available_qty(product)
            shortage = max(qty - available_before, 0)

            item_status = (
                OrderStockItem.STOCK_LACK
                if shortage > 0
                else OrderStockItem.LINKED
            )

            OrderStockItem.objects.create(
                order=order,
                link=link,
                seller=order.seller,
                product=product,
                quantity=qty,
                reserved_qty=qty,
                shortage_qty=shortage,
                status=item_status,
                raw_product_text=product.name,
            )

            create_movement(
                seller=order.seller,
                product=product,
                order=order,
                movement_type=(
                    StockMovement.STOCK_LACK
                    if shortage > 0
                    else StockMovement.ORDER_RESERVED
                ),
                qty_delta=-qty,
                actor=actor,
                note=(
                    f"Mixed order reserved: {product.name} x{qty}. "
                    f"Available before: {available_before}"
                ),
            )

            total_reserved += qty
            total_shortage += shortage
            product_desc_parts.append(f"{product.name} x{qty}")

        if total_reserved <= 0:
            link.status = OrderStockLink.UNMATCHED
            link.reserved_qty = 0
            link.shortage_qty = 0
            link.raw_product_text = order.product_desc or ""
            link.save()
            return link

        link.status = (
            OrderStockLink.STOCK_LACK
            if total_shortage > 0
            else OrderStockLink.LINKED
        )
        link.reserved_qty = total_reserved
        link.shortage_qty = total_shortage
        link.raw_product_text = " + ".join(product_desc_parts)
        link.save()

        order.product_desc = " + ".join(product_desc_parts)
        order.quantity = total_reserved
        order._skip_stock_signal = True
        order.save(update_fields=["product_desc", "quantity"])

        return link


def set_order_stock_items_from_json(order, stock_items_json: str, actor=None):
    """
    Used by create/edit order view.
    """
    try:
        items_data = json.loads(stock_items_json or "[]")
    except Exception:
        items_data = []

    if not isinstance(items_data, list) or not items_data:
        return None

    return set_order_stock_items(
        order=order,
        items_data=items_data,
        actor=actor,
    )


def auto_link_order_stock(order, actor=None) -> OrderStockLink:
    """
    Use after order create/import/edit.
    It never blocks order creation.
    If cannot match stock, it marks UNMATCHED.
    """
    setting = get_seller_inventory_setting(order.seller)

    if setting.stock_mode == InventorySellerSetting.NO_STOCK:
        return set_order_stock(
            order=order,
            product=None,
            qty=order.quantity,
            raw_text=order.product_desc or "",
            actor=actor,
            note="Seller does not require stock",
        )

    product = match_product(order.seller, order.product_desc or "")

    if product:
        return set_order_stock(
            order=order,
            product=product,
            qty=order.quantity,
            raw_text=order.product_desc or "",
            actor=actor,
            note="Auto linked stock from order product description",
        )

    return set_order_stock(
        order=order,
        product=None,
        qty=order.quantity,
        raw_text=order.product_desc or "",
        actor=actor,
        note="Could not auto match stock",
    )


def sync_order_status_stock(
    *,
    order,
    old_status: str | None,
    new_status: str | None,
    actor=None,
):
    """
    Call when order status changes.

    VOID = stock back
    RETURNED = stock back
    DELIVERED = stock not back, only clear reserved
    """
    old_status = str(old_status or "").upper()
    new_status = str(new_status or "").upper()

    if old_status == new_status:
        return None

    if new_status in ["VOID", "CANCELLED", "CANCELED"]:
        release_order_stock(
            order,
            actor=actor,
            note=f"Order status changed to {new_status}",
        )
        release_order_stock_items(
            order,
            actor=actor,
            note=f"Order status changed to {new_status}",
        )
        return None

    if new_status in ["RETURNED"]:
        return_order_stock_good(
            order,
            actor=actor,
            note=f"Order status changed to {new_status}",
        )
        return None

    if new_status in ["DELIVERED", "DONE"]:
        mark_order_delivered(
            order,
            actor=actor,
            note=f"Order status changed to {new_status}",
        )
        return None

    return None


def save_alias_from_order_link(link: OrderStockLink, actor=None):
    """
    After staff manually chooses product for imported unmatched order,
    save alias so next import can auto match.
    """
    if not link.product or not link.raw_product_text:
        return None

    raw = link.raw_product_text.strip()

    if not raw:
        return None

    alias, created = StockAlias.objects.get_or_create(
        seller=link.seller,
        alias_text=raw,
        defaults={
            "product": link.product,
            "created_by": actor,
        },
    )

    if not created and alias.product_id != link.product_id:
        alias.product = link.product
        alias.created_by = actor
        alias.save(update_fields=["product", "created_by"])

    return alias
