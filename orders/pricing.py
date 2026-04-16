from decimal import Decimal

from masterdata.models import SellerPriceRule


def apply_pricing(order):
    seller = order.seller
    shipper = order.delivery_shipper

    if not seller or not shipper:
        return

    rule_type = "COD" if (order.cod or Decimal("0")) > 0 else "PV"

    rule = (
        SellerPriceRule.objects.filter(
            seller=seller,
            shipper=shipper,
            rule_type=rule_type,
            is_active=True,
        )
        .order_by("-id")
        .first()
    )

    if not rule:
        return

    delivery_fee = rule.delivery_fee or Decimal("0")
    additional_fee = rule.additional_fee or Decimal("0")
    percent_cod = rule.percent_cod or Decimal("0")

    if rule_type == "COD" and percent_cod:
        if percent_cod > 1:
            percent_cod = percent_cod / Decimal("100")
        additional_fee += (order.cod or Decimal("0")) * percent_cod

    shipper_type = (getattr(shipper, "shipper_type", "") or "").upper()

    if shipper_type == "PROVINCE":
        order.province_fee = delivery_fee
    else:
        order.delivery_fee = delivery_fee

    order.additional_fee = additional_fee

    if rule.is_locked:
        order.is_locked = True