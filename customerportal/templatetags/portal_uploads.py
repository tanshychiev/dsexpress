from django import template

register = template.Library()


@register.simple_tag
def customer_upload_pending_count():
    try:
        from customerportal.models import SellerUploadBatch

        return SellerUploadBatch.objects.filter(
            status=SellerUploadBatch.STATUS_PENDING,
        ).count()
    except Exception:
        return 0
