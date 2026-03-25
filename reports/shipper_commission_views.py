from __future__ import annotations

import tempfile
from datetime import datetime, time
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from PIL import Image
from playwright.sync_api import sync_playwright

from deliverpp.models import PPDeliveryBatch, PPDeliveryItem
from .commission_excel import export_shipper_commission_excel
from .commission_services import build_shipper_commission_report


def _parse_date_start(value: str):
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
        dt = datetime.combine(d, time.min)
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except Exception:
        return None


def _parse_date_end(value: str):
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
        dt = datetime.combine(d, time.max)
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except Exception:
        return None


def _empty_report():
    return {
        "shipper_groups": [],
        "grand_total": {
            "morning_assign": 0,
            "afternoon_assign": 0,
            "done_morning": 0,
            "done_afternoon": 0,
            "total_done_pc": 0,
            "commission_pc": 0,
            "commission_khr": 0,
        },
    }


def _build_report(date_from="", date_to=""):
    dt_from = _parse_date_start(date_from)
    dt_to = _parse_date_end(date_to)

    batch_qs = (
        PPDeliveryBatch.objects
        .select_related("shipper")
        .prefetch_related("items")
        .filter(assigned_at__isnull=False)
        .order_by("assigned_at", "id")
    )

    item_qs = (
        PPDeliveryItem.objects
        .select_related("batch", "batch__shipper", "order")
        .filter(batch__assigned_at__isnull=False)
        .order_by("batch__assigned_at", "id")
    )

    # IMPORTANT:
    # Commission report follows BATCH ASSIGN DATE/TIME for both assign and done.
    if dt_from:
        batch_qs = batch_qs.filter(assigned_at__gte=dt_from)
        item_qs = item_qs.filter(batch__assigned_at__gte=dt_from)

    if dt_to:
        batch_qs = batch_qs.filter(assigned_at__lte=dt_to)
        item_qs = item_qs.filter(batch__assigned_at__lte=dt_to)

    start_date = dt_from.date() if dt_from else None
    end_date = dt_to.date() if dt_to else None

    return build_shipper_commission_report(
        pp_batches=list(batch_qs),
        pp_items=list(item_qs),
        start_date=start_date,
        end_date=end_date,
    )


@login_required
def shipper_commission_report(request):
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    searched = request.GET.get("search") == "1"
    action = (request.GET.get("action") or "show").strip().lower()

    report = _empty_report()

    if searched:
        report = _build_report(date_from=date_from, date_to=date_to)

        if action == "excel":
            return export_shipper_commission_excel(
                report=report,
                date_from=date_from,
                date_to=date_to,
            )

    return render(
        request,
        "reports/shipper_commission_report.html",
        {
            "searched": searched,
            "date_from": date_from,
            "date_to": date_to,
            "report": report,
        },
    )


@login_required
def shipper_commission_report_pdf(request):
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    report = _build_report(date_from=date_from, date_to=date_to)

    html = render_to_string(
        "reports/shipper_commission_report_pdf.html",
        {
            "searched": True,
            "date_from": date_from,
            "date_to": date_to,
            "report": report,
        },
        request=request,
    )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode="w", encoding="utf-8") as f:
        f.write(html)
        temp_html = f.name

    temp_png = f"{temp_html}.png"
    temp_pdf = f"{temp_html}.pdf"
    pdf_bytes = b""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1600, "height": 2400},
                device_scale_factor=2,
            )
            page.goto(Path(temp_html).as_uri(), wait_until="networkidle")
            page.screenshot(path=temp_png, full_page=True)
            browser.close()

        image = Image.open(temp_png)

        if image.mode == "RGBA":
            bg = Image.new("RGB", image.size, (255, 255, 255))
            bg.paste(image, mask=image.split()[3])
            image = bg
        elif image.mode != "RGB":
            image = image.convert("RGB")

        image.save(temp_pdf, "PDF", resolution=100.0)

        with open(temp_pdf, "rb") as f:
            pdf_bytes = f.read()

    finally:
        Path(temp_html).unlink(missing_ok=True)
        Path(temp_png).unlink(missing_ok=True)
        Path(temp_pdf).unlink(missing_ok=True)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="shipper_commission_report.pdf"'
    return response