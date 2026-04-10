from decimal import Decimal
from masterdata.models import SellerPriceRule

def apply_pricing(order):
    seller = order.seller
    shipper = order.delivery_shipper

    if not seller or not shipper:
        return

    # detect type
    rule_type = "COD" if order.cod and order.cod > 0 else "PV"

    rule = SellerPriceRule.objects.filter(
        seller=seller,
        shipper=shipper,
        rule_type=rule_type,
        is_active=True
    ).first()

    if not rule:
        return

    # apply fee
    order.delivery_fee = rule.delivery_fee

    additional = rule.additional_fee

    if rule_type == "COD" and rule.percent_cod:
        additional += (order.cod or Decimal("0")) * rule.percent_cod

    order.additional_fee = additional

    # lock order
    if rule.is_locked:
        order.is_locked = True