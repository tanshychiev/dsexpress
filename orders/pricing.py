from decimal import Decimal
from masterdata.models import SellerPriceRule

def apply_pricing(order):
    seller = order.seller
    shipper = order.delivery_shipper

    print("========== PRICING DEBUG ==========")
    print("order id =", getattr(order, "id", None))
    print("seller =", seller)
    print("shipper =", shipper)
    print("cod =", order.cod)

    if not seller:
        print("STOP: no seller")
        return

    rule_type = "COD" if (order.cod or Decimal("0")) > 0 else "PV"
    print("rule_type =", rule_type)

    rule = None

    if shipper:
        rule = SellerPriceRule.objects.filter(
            seller=seller,
            shipper=shipper,
            rule_type=rule_type,
            is_active=True
        ).first()
        print("exact rule =", rule)

    if not rule:
        rule = SellerPriceRule.objects.filter(
            seller=seller,
            shipper__isnull=True,
            rule_type=rule_type,
            is_active=True
        ).first()
        print("fallback rule =", rule)

    if not rule:
        print("STOP: no rule matched")
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

    print("delivery_fee =", order.delivery_fee)
    print("additional_fee =", order.additional_fee)
    print("is_locked =", order.is_locked)
    print("========== END PRICING DEBUG ==========")