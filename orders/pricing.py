from decimal import Decimal

from masterdata.models import SellerPriceRule


def apply_pricing(order):
    seller = getattr(order, "seller", None)
    shipper = getattr(order, "delivery_shipper", None)

    if not seller or not shipper:
        return

    cod_value = order.cod or Decimal("0")
    price_value = order.price or Decimal("0")
    shipper_type = (getattr(shipper, "shipper_type", "") or "").upper()

    # Province orders often force COD to 0 after assign,
    # so province should try COD rule first, then fallback to PV.
    if shipper_type == "PROVINCE":
        rule = (
            SellerPriceRule.objects.filter(
                seller=seller,
                shipper=shipper,
                rule_type="COD",
                is_active=True,
            )
            .order_by("-id")
            .first()
        )
        if not rule:
            rule = (
                SellerPriceRule.objects.filter(
                    seller=seller,
                    shipper=shipper,
                    rule_type="PV",
                    is_active=True,
                )
                .order_by("-id")
                .first()
            )
    else:
        rule_type = "COD" if cod_value > 0 else "PV"
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
    percent_value = rule.percent_cod or Decimal("0")

    if percent_value:
        if percent_value > 1:
            percent_value = percent_value / Decimal("100")

        if shipper_type == "PROVINCE":
            base_amount = price_value
        else:
            base_amount = cod_value

        additional_fee += base_amount * percent_value

    if shipper_type == "PROVINCE":
        order.province_fee = delivery_fee
    else:
        order.delivery_fee = delivery_fee

    order.additional_fee = additional_fee

    if getattr(rule, "is_locked", False):
        order.is_locked = True