from .models import OrderActivity


def add_order_activity(order, action, user=None, shipper=None, old_status="", new_status="", note=""):
    OrderActivity.objects.create(
        order=order,
        action=action,
        actor=user,
        shipper=shipper,
        old_status=old_status or "",
        new_status=new_status or "",
        note=note or "",
    )