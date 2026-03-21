from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("module", "record", "action", "user", "created_at")
    list_filter = ("module", "action")
