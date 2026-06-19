from decimal import Decimal

from masterdata.models import SellerPriceRule


ZERO = Decimal("0.00")


def normalize_percent(value):
    try:
        percent = Decimal(str(value or 0))
    except Exception:
        return ZERO

    if percent > 1:
        percent = percent / Decimal("100")

    return percent


def get_pricing_rule(order, original_cod=None):
    seller = getattr(order, "seller", None)
    shipper = getattr(order, "delivery_shipper", None)

    if not seller or not shipper:
        return None

    shipper_type = (
        getattr(shipper, "shipper_type", "") or ""
    ).upper()

    if original_cod is None:
        cod_value = getattr(order, "cod", ZERO) or ZERO
    else:
        cod_value = Decimal(str(original_cod or 0))

    if shipper_type == "PROVINCE":
        rule_type = (
            SellerPriceRule.TYPE_COD
            if cod_value > ZERO
            else SellerPriceRule.TYPE_PV
        )

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

        # Fallback if the selected rule does not exist.
        if not rule:
            fallback_type = (
                SellerPriceRule.TYPE_PV
                if rule_type == SellerPriceRule.TYPE_COD
                else SellerPriceRule.TYPE_COD
            )

            rule = (
                SellerPriceRule.objects.filter(
                    seller=seller,
                    shipper=shipper,
                    rule_type=fallback_type,
                    is_active=True,
                )
                .order_by("-id")
                .first()
            )

        return rule

    rule_type = (
        SellerPriceRule.TYPE_COD
        if cod_value > ZERO
        else SellerPriceRule.TYPE_PV
    )

    return (
        SellerPriceRule.objects.filter(
            seller=seller,
            shipper=shipper,
            rule_type=rule_type,
            is_active=True,
        )
        .order_by("-id")
        .first()
    )


def apply_pricing(
    order,
    *,
    original_cod=None,
    defer_province_cod_fee=False,
):
    seller = getattr(order, "seller", None)
    shipper = getattr(order, "delivery_shipper", None)

    if not seller or not shipper:
        return None

    if original_cod is None:
        cod_value = getattr(order, "cod", ZERO) or ZERO
    else:
        cod_value = Decimal(str(original_cod or 0))

    price_value = getattr(order, "price", ZERO) or ZERO

    shipper_type = (
        getattr(shipper, "shipper_type", "") or ""
    ).upper()

    rule = get_pricing_rule(
        order,
        original_cod=cod_value,
    )

    if not rule:
        return None

    delivery_fee = rule.delivery_fee or ZERO
    fixed_additional_fee = rule.additional_fee or ZERO
    percent_rate = normalize_percent(rule.percent_cod)

    calculated_additional_fee = fixed_additional_fee

    if percent_rate:
        if shipper_type == "PROVINCE":
            percent_base = price_value
        else:
            percent_base = cod_value

        calculated_additional_fee += (
            percent_base * percent_rate
        )

    if shipper_type == "PROVINCE":
        order.delivery_fee = ZERO
        order.province_fee = delivery_fee

        # Province COD extra fee is deducted only after
        # J&T/bus pays DS Express.
        if defer_province_cod_fee and cod_value > ZERO:
            order.additional_fee = ZERO
        else:
            order.additional_fee = calculated_additional_fee

    else:
        order.province_fee = ZERO
        order.delivery_fee = delivery_fee
        order.additional_fee = calculated_additional_fee

    if getattr(rule, "is_locked", False):
        order.is_locked = True

    return rule