from decimal import Decimal
from masterdata.models import SellerPriceRule

def apply_pricing(order):
    seller = order.seller
    shipper = order.delivery_shipper

    if not seller:
        return

    rule_type = "COD" if (order.cod or Decimal("0")) > 0 else "PV"

    rule = None

    if shipper:
        rule = SellerPriceRule.objects.filter(
            seller=seller,
            shipper=shipper,
            rule_type=rule_type,
            is_active=True
        ).first()

    if not rule:
        rule = SellerPriceRule.objects.filter(
            seller=seller,
            shipper__isnull=True,
            rule_type=rule_type,
            is_active=True
        ).first()

    if not rule:
        return

    order.delivery_fee = rule.delivery_fee or Decimal("0")

    additional = rule.additional_fee or Decimal("0")

    if rule_type == "COD" and rule.percent_cod:
        percent = rule.percent_cod
        if percent > 1:
            percent = percent / Decimal("100")
        additional += (order.cod or Decimal("0")) * percent

    order.additional_fee = additional

    if rule.is_locked:
        order.is_locked = True