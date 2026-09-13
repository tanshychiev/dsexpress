from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0021_systemlock"),
    ]

    operations = [
        migrations.CreateModel(
            name="PickupBooking",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("method", models.CharField(choices=[("CUSTOMER_PICKUP", "Customer Pickup"), ("GRAB", "Grab / External Rider")], max_length=30)),
                ("cod", models.DecimalField(decimal_places=2, default="0.00", max_digits=10)),
                ("delivery_fee", models.DecimalField(decimal_places=2, default="0.00", max_digits=10)),
                ("additional_fee", models.DecimalField(decimal_places=2, default="0.00", max_digits=10)),
                ("completed_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("completed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pickup_bookings_completed", to=settings.AUTH_USER_MODEL)),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="pickup_booking", to="orders.order")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pickup_bookings_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Pickup / Booking", "verbose_name_plural": "Pickup / Booking", "ordering": ["-completed_at", "-id"]},
        ),
        migrations.CreateModel(
            name="PickupBookingAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("COMPLETED", "Completed"), ("UPDATED", "Updated")], max_length=20)),
                ("old_values", models.JSONField(blank=True, default=dict)),
                ("new_values", models.JSONField(blank=True, default=dict)),
                ("changed_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("booking", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audit_logs", to="pickupbooking.pickupbooking")),
                ("changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pickup_booking_audits", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-changed_at", "-id"]},
        ),
    ]
