from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
import tempfile

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone

from PIL import Image
from playwright.sync_api import sync_playwright

from deliverpp.models import ClearPPCOD
from .shipper_cod_services import build_shipper_cod_report


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


def _get_report_data(date_from: str, date_to: str):
    qs = (
        ClearPPCOD.objects
        .select_related("batch", "batch__shipper")
        .filter(batch__assigned_at__isnull=False)
        .order_by("batch__assigned_at", "id")
    )

    dt_from = _parse_date_start(date_from)
    dt_to = _parse_date_end(date_to)

    if dt_from:
        qs = qs.filter(batch__assigned_at__gte=dt_from)
    if dt_to:
        qs = qs.filter(batch__assigned_at__lte=dt_to)

    return build_shipper_cod_report(list(qs))


@login_required
def shipper_cod_report(request):
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    searched = request.GET.get("search") == "1"

    report = {
        "days": [],
        "grand_morning_total": {},
        "grand_afternoon_total": {},
        "grand_total": {},
    }

    if searched:
        report = _get_report_data(date_from, date_to)

    return render(
        request,
        "reports/shipper_cod_report.html",
        {
            "searched": searched,
            "date_from": date_from,
            "date_to": date_to,
            "report": report,
        },
    )


@login_required
def shipper_cod_report_pdf(request):
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    report = _get_report_data(date_from, date_to)

    html = render_to_string(
        "reports/shipper_cod_report_pdf.html",
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
                viewport={"width": 1600, "height": 2200},
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
    response["Content-Disposition"] = 'attachment; filename="shipper_cod_report.pdf"'
    return response