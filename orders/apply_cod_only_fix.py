from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if old in text:
        return text.replace(old, new, 1), True
    if new in text:
        print(f"[OK] {label} was already fixed.")
        return text, False
    raise RuntimeError(f"Could not find the expected code for: {label}")


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()

    views_path = root / "orders" / "views.py"
    template_path = root / "templates" / "orders" / "order_edit.html"

    if not views_path.exists():
        print(f"ERROR: Not found: {views_path}")
        print(r"Run this script from E:\dsexpress_2 or pass that folder as an argument.")
        return 1

    if not template_path.exists():
        print(f"ERROR: Not found: {template_path}")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    views_backup = views_path.with_name(f"views.py.backup_{stamp}")
    template_backup = template_path.with_name(f"order_edit.html.backup_{stamp}")

    shutil.copy2(views_path, views_backup)
    shutil.copy2(template_path, template_backup)

    views = views_path.read_text(encoding="utf-8")
    template = template_path.read_text(encoding="utf-8")

    changed = False

    old_lock_block = '''    # Province orders stay locked to protect COD, fees, goods and status.
    # After "Undo Complete", the batch becomes PENDING again. In that case,
    # staff may edit only the receiver phone and address.
    province_pending = False
    if order.is_locked:
        try:
            from provinceops.models import ProvinceBatch, ProvinceBatchItem

            province_pending = ProvinceBatchItem.objects.filter(
                order=order,
                batch__status=ProvinceBatch.STATUS_PENDING,
            ).exists()
        except Exception:
            province_pending = False

        if not province_pending:
            messages.error(request, "This order is locked and cannot be edited.")
            return redirect("order_detail", pk=order.id)

    receiver_only_edit = bool(order.is_locked and province_pending)

    is_admin = (
'''

    new_lock_block = '''    # PP completed = DELIVERED. Province completed = DONE (or done_at set).
    # Completed and locked orders may open the full edit form.
    normalized_status = str(order.status or "").strip().upper()
    delivered_status = str(Order.STATUS_DELIVERED or "").strip().upper()
    is_done_or_delivered = (
        normalized_status in {delivered_status, "DONE"}
        or bool(order.done_at)
    )

    # All normal fields are editable. Only COD is protected.
    receiver_only_edit = False
    cod_is_protected = bool(order.is_locked or is_done_or_delivered)

    is_admin = (
'''

    views, did = replace_once(
        views,
        old_lock_block,
        new_lock_block,
        "remove the full-order lock",
    )
    changed |= did

    old_status_block = '''    is_done_or_delivered = (
        order.status == Order.STATUS_DELIVERED
        or bool(order.done_at)
    )

'''
    if old_status_block in views:
        views = views.replace(old_status_block, "", 1)
        changed = True

    replacements = [
        (
            "        if is_done_or_delivered and posted_cod != old_cod_decimal:",
            "        if cod_is_protected and posted_cod != old_cod_decimal:",
            "protect COD only",
        ),
        (
            '''                "COD cannot be updated after delivered/done. "
                "Only admin or correct override password can change it."''',
            '''                "COD is locked for this order. "
                "Only admin or the correct override password can change it."''',
            "COD validation message",
        ),
        (
            "                    elif is_admin and is_done_or_delivered:",
            "                    elif is_admin and cod_is_protected:",
            "admin COD audit condition 1",
        ),
        (
            '                        audit_note = "Edited order | COD changed by admin after delivered/done"',
            '                        audit_note = "Edited order | Locked COD changed by admin"',
            "admin COD audit note",
        ),
        (
            "                    elif is_admin and is_done_or_delivered:",
            "                    elif is_admin and cod_is_protected:",
            "admin COD audit condition 2",
        ),
        (
            '                        cod_note += " (changed by admin after delivered/done)"',
            '                        cod_note += " (locked COD changed by admin)"',
            "admin COD timeline note",
        ),
        (
            '''            "is_done_or_delivered": is_done_or_delivered,
            "is_admin_user": is_admin,''',
            '''            "is_done_or_delivered": is_done_or_delivered,
            "cod_is_protected": cod_is_protected,
            "is_admin_user": is_admin,''',
            "send COD protection flag to template",
        ),
    ]

    for old, new, label in replacements:
        if old in views:
            views = views.replace(old, new, 1)
            changed = True
        elif new in views:
            print(f"[OK] {label} was already fixed.")
        else:
            print(f"[WARN] Could not find optional code for: {label}")

    old_detail = '''@login_required
def order_detail(request: HttpRequest, pk: int):
    order = get_object_or_404(Order, pk=pk, is_deleted=False)
    return render(request, "orders/order_detail.html", {"order": order})
'''
    new_detail = '''@login_required
def order_detail(request: HttpRequest, pk: int):
    # Keep legacy /detail/ links working without a missing template.
    get_object_or_404(Order, pk=pk, is_deleted=False)
    return redirect("order_created", pk=pk)
'''
    views, did = replace_once(
        views,
        old_detail,
        new_detail,
        "legacy order detail redirect",
    )
    changed |= did

    template_replacements = [
        (
            '<div class="page-title">{% if receiver_only_edit %}Edit Receiver Phone &amp; Address{% else %}Edit Order{% endif %}</div>',
            '<div class="page-title">Edit Order</div>',
            "edit page title",
        ),
        (
            '''    {% if receiver_only_edit %}
      <div class="warn">
        This province shipment is pending after Undo Complete. Only the receiver phone and address can be changed.
      </div>
    {% elif is_done_or_delivered %}
      <div class="warn">
        This order is already Delivered / Done. You can still edit normal fields, but COD can only be changed by admin or with override password.
      </div>
    {% endif %}''',
            '''    {% if cod_is_protected %}
      <div class="warn">
        You can edit all order information. COD is locked and can only be changed by admin or with the override password.
      </div>
    {% endif %}''',
            "COD-only warning",
        ),
        (
            '                {% if is_done_or_delivered %}data-protected="1"{% endif %}',
            '                {% if cod_is_protected and not is_admin_user %}data-protected="1"{% endif %}',
            "COD field protection",
        ),
        (
            '<input name="receiver_name" value="{{ order.receiver_name|default:\'\' }}" {% if receiver_only_edit %}readonly{% endif %}>',
            '<input name="receiver_name" value="{{ order.receiver_name|default:\'\' }}">',
            "receiver name editing",
        ),
        (
            '<button class="btn btn-primary" type="submit" id="saveBtn">{% if receiver_only_edit %}Save Receiver Info{% else %}Save{% endif %}</button>',
            '<button class="btn btn-primary" type="submit" id="saveBtn">Save</button>',
            "save button text",
        ),
        (
            "      This order is already Delivered / Done. Enter password to confirm COD change.",
            "      COD is locked for this order. Enter the override password to confirm the COD change.",
            "COD password dialog",
        ),
    ]

    for old, new, label in template_replacements:
        if old in template:
            template = template.replace(old, new, 1)
            changed = True
        elif new in template:
            print(f"[OK] {label} was already fixed.")
        else:
            print(f"[WARN] Could not find optional template code for: {label}")

    wrapper_patterns = [
        (
            '''      {% if not receiver_only_edit %}
      <div class="section">''',
            '''      <div class="section">''',
        ),
        (
            '''      </div>
      {% endif %}

      <div class="section">''',
            '''      </div>

      <div class="section">''',
        ),
        (
            '''      {% if not receiver_only_edit %}
      <div class="section">
        <div class="section-head">Remark / Reason</div>''',
            '''      <div class="section">
        <div class="section-head">Remark / Reason</div>''',
        ),
        (
            '''      </div>

      {% endif %}

      <div class="btns">''',
            '''      </div>

      <div class="btns">''',
        ),
    ]

    for old, new in wrapper_patterns:
        if old in template:
            template = template.replace(old, new, 1)
            changed = True

    if 'messages.error(request, "This order is locked and cannot be edited.")' in views:
        raise RuntimeError("The old full-order lock is still present after patching.")

    if "cod_is_protected" not in views:
        raise RuntimeError("cod_is_protected was not added to views.py.")

    if "cod_is_protected" not in template:
        raise RuntimeError("cod_is_protected was not added to order_edit.html.")

    views_path.write_text(views, encoding="utf-8", newline="\n")
    template_path.write_text(template, encoding="utf-8", newline="\n")

    print()
    print("SUCCESS: COD-only lock fix applied.")
    print(f"Updated: {views_path}")
    print(f"Updated: {template_path}")
    print(f"Backup:  {views_backup}")
    print(f"Backup:  {template_backup}")
    print()
    print("Verify with:")
    print(r'  findstr /n /c:"This order is locked and cannot be edited." orders\views.py')
    print(r'  findstr /n /c:"cod_is_protected" orders\views.py')
    print()
    print("Then run:")
    print("  git add orders/views.py templates/orders/order_edit.html")
    print('  git commit -m "Remove order lock and protect COD only"')
    print("  git push origin main")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
