from pathlib import Path
import re

ROOT = Path.cwd()
views_path = ROOT / "orders" / "views.py"
template_path = ROOT / "templates" / "orders" / "order_list.html"

if not views_path.exists():
    raise SystemExit(f"Not found: {views_path}")
if not template_path.exists():
    raise SystemExit(f"Not found: {template_path}")

views = views_path.read_text(encoding="utf-8")

old_import = "from django.db.models import Count, F, Q"
new_import = "from django.db.models import Count, F, Q, OuterRef, Subquery, DateTimeField"
if old_import in views and new_import not in views:
    views = views.replace(old_import, new_import, 1)

needle = '''    total_results = qs.count()

    paginator = Paginator(qs, 50)'''

replacement = '''    # Exact completion timestamp for the Orders list.
    # Keep Order.done_at unchanged because it is a DateField.
    delivered_activity_at = (
        OrderActivity.objects
        .filter(
            order_id=OuterRef("pk"),
            new_status=Order.STATUS_DELIVERED,
        )
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    qs = qs.annotate(
        complete_at=Subquery(
            delivered_activity_at,
            output_field=DateTimeField(),
        )
    )

    total_results = qs.count()

    paginator = Paginator(qs, 50)'''

if "complete_at=Subquery(" not in views:
    if needle not in views:
        raise SystemExit(
            "Could not find the expected order_list paginator block. No changes were written."
        )
    views = views.replace(needle, replacement, 1)

views_path.write_text(views, encoding="utf-8")

tpl = template_path.read_text(encoding="utf-8")

pattern = re.compile(
    r'''<td[^>]*data-label=["']Complete Date["'][^>]*>.*?</td>''',
    re.I | re.S
)

cell = '''<td data-label="Complete Date">
                  {% if o.pickup_booking and o.pickup_booking.completed_at %}
                    {{ o.pickup_booking.completed_at|date:"Y-m-d H:i" }}
                  {% elif o.complete_at %}
                    {{ o.complete_at|date:"Y-m-d H:i" }}
                  {% elif o.done_at %}
                    {{ o.done_at|date:"Y-m-d" }}
                  {% else %}
                    -
                  {% endif %}
                </td>'''

if pattern.search(tpl):
    tpl = pattern.sub(cell, tpl, count=1)
else:
    shipper_marker = '<td data-label="Shipper">'
    if shipper_marker not in tpl:
        raise SystemExit(
            "Could not find the Shipper table cell. views.py was updated, but template was not changed."
        )
    tpl = tpl.replace(
        shipper_marker,
        cell + "\n\n                " + shipper_marker,
        1,
    )

template_path.write_text(tpl, encoding="utf-8")

print("PATCH OK")
print("Preserved the rest of orders/views.py, including system-lock functions.")
print("Complete Date now uses:")
print("  Pickup/Booking -> pickup_booking.completed_at")
print("  Normal delivery -> latest DELIVERED OrderActivity.created_at")
print("  Fallback -> done_at date")
