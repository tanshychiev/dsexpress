from django.db import models
from django.contrib.auth.models import User


class AuditLog(models.Model):
    module = models.CharField(max_length=50)
    record = models.CharField(max_length=100)
    action = models.CharField(max_length=50)
    detail = models.TextField(blank=True, null=True)

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.module} {self.action} {self.record}"
