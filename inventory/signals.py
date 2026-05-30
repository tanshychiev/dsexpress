from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from orders.models import Order

from .services import sync_order_status_stock


@receiver(pre_save, sender=Order)
def remember_old_order_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._old_status_for_stock = None
        return

    try:
        old = Order.objects.only("status").get(pk=instance.pk)
        instance._old_status_for_stock = old.status
    except Order.DoesNotExist:
        instance._old_status_for_stock = None


@receiver(post_save, sender=Order)
def sync_stock_after_order_status_change(sender, instance, created, **kwargs):
    """
    IMPORTANT:
    Do not auto-link stock when order is created.

    Why:
    create_order/import_orders already call:
    - auto_link_order_stock()
    - set_order_stock_items()

    If signal also auto-links on create, history becomes:
    -20, +20, -20

    So signal only handles status change:
    - VOID/CANCEL = stock back
    - RETURNED = stock back
    - DELIVERED/DONE = clear reserved
    """

    if getattr(instance, "_skip_stock_signal", False):
        return

    if getattr(instance, "is_deleted", False):
        return

    if created:
        return

    old_status = getattr(instance, "_old_status_for_stock", None)

    if old_status and old_status != instance.status:
        try:
            sync_order_status_stock(
                order=instance,
                old_status=old_status,
                new_status=instance.status,
                actor=getattr(instance, "updated_by", None),
            )
        except Exception:
            pass