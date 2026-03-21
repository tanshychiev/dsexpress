from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from orders.models import Order
from reportbot.models import ShopDailyReport, ShopDailyReportStatusLog


def _money(v):
    return Decimal(str(v or 0))


def generate_report_code(report_date, shop_id: int) -> str:
    return f"REP-{report_date.strftime('%Y%m%d')}-{shop_id}"


def get_active_shops_for_day(report_date):
    return (
        Order.objects.filter(
            done_at=report_date,
            status__in=["DELIVERED", "DONE"],
            seller__isnull=False,
            is_deleted=False,
        )
        .values_list("seller_id", flat=True)
        .distinct()
    )


def build_shop_day_data(shop, report_date):
    done_qs = Order.objects.filter(
        seller=shop,
        done_at=report_date,
        status__in=["DELIVERED", "DONE"],
        is_deleted=False,
    ).select_related("seller", "delivery_shipper")

    pending_qs = Order.objects.filter(
        seller=shop,
        created_at__date=report_date,
        is_deleted=False,
    ).exclude(status__in=["DELIVERED", "DONE", "VOID"]).select_related("seller", "delivery_shipper")

    done_count = done_qs.count()
    pending_count = pending_qs.count()

    total_cod = sum((_money(x.cod) for x in done_qs), Decimal("0.00"))
    total_fee = sum(((_money(x.delivery_fee) + _money(x.additional_fee)) for x in done_qs), Decimal("0.00"))
    total_pay = total_cod - total_fee

    return {
        "done_rows": list(done_qs),
        "pending_rows": list(pending_qs),
        "done_count": done_count,
        "pending_count": pending_count,
        "total_cod": total_cod,
        "total_fee": total_fee,
        "total_pay": total_pay,
    }


def render_simple_report_png(report, report_data):
    """
    Temporary placeholder PNG path.
    Later we will replace this with real image generation from report layout.
    """
    base_dir = Path(settings.BASE_DIR) / "media" / "daily_reports"
    base_dir.mkdir(parents=True, exist_ok=True)

    # placeholder filename now
    filename = f"{report.report_code}_{uuid4().hex[:8]}.png"
    path = base_dir / filename

    # create a simple text file placeholder for now if png generation not implemented yet
    # later replace with real PNG drawing
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 1000), "white")
    draw = ImageDraw.Draw(img)

    y = 30
    lines = [
        "DS EXPRESS DAILY REPORT",
        f"Report Code: {report.report_code}",
        f"Shop: {report.shop.name}",
        f"Date: {report.report_date}",
        "",
        f"Done: {report_data['done_count']}",
        f"Pending: {report_data['pending_count']}",
        f"COD: ${report_data['total_cod']}",
        f"Fee: ${report_data['total_fee']}",
        f"Pay: ${report_data['total_pay']}",
        "",
        "Status: WAITING_CHECK",
        "",
        "React in Telegram:",
        "✅ Approve",
        "⚠️ Need Fix",
        "⏸️ Hold",
    ]

    for line in lines:
        draw.text((40, y), line, fill="black")
        y += 42

    img.save(path)
    return str(path)


def create_or_update_daily_report(shop, report_date):
    report_code = generate_report_code(report_date, shop.id)
    data = build_shop_day_data(shop, report_date)

    report, _ = ShopDailyReport.objects.get_or_create(
        shop=shop,
        report_date=report_date,
        defaults={
            "report_code": report_code,
        },
    )

    report.report_code = report_code
    report.done_count = data["done_count"]
    report.pending_count = data["pending_count"]
    report.total_cod = data["total_cod"]
    report.total_fee = data["total_fee"]
    report.total_pay = data["total_pay"]
    report.status = ShopDailyReport.STATUS_WAITING_CHECK
    report.reaction_emoji = ""
    report.approved_by = None
    report.approved_at = None
    report.telegram_actor_id = ""
    report.telegram_actor_name = ""

    png_path = render_simple_report_png(report, data)
    report.png_path = png_path
    report.save()

    return report, data


def apply_telegram_reaction(report, emoji: str, actor_id: str, actor_name: str):
    emoji_to_status = {
        "✅": ShopDailyReport.STATUS_APPROVED,
        "⚠️": ShopDailyReport.STATUS_NEED_FIX,
        "⏸️": ShopDailyReport.STATUS_HOLD,
    }

    if emoji not in emoji_to_status:
        return False, "Unsupported emoji"

    old_status = report.status
    new_status = emoji_to_status[emoji]

    report.status = new_status
    report.reaction_emoji = emoji
    report.telegram_actor_id = actor_id
    report.telegram_actor_name = actor_name
    report.approved_at = timezone.now()
    report.save(update_fields=[
        "status",
        "reaction_emoji",
        "telegram_actor_id",
        "telegram_actor_name",
        "approved_at",
        "updated_at",
    ])

    ShopDailyReportStatusLog.objects.create(
        report=report,
        old_status=old_status,
        new_status=new_status,
        emoji=emoji,
        actor_name=actor_name,
        actor_telegram_id=actor_id,
    )

    return True, new_status