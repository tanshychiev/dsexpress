from .models import AuditLog


def add_audit_log(module, obj, action, user=None, field_name="", old_value="", new_value="", note=""):
    AuditLog.objects.create(
        module=module,
        object_id=obj.pk,
        object_repr=str(obj),
        action=action,
        field_name=field_name or "",
        old_value=str(old_value or ""),
        new_value=str(new_value or ""),
        note=note or "",
        created_by=user,
    )