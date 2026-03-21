from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from masterdata.models import Seller
from reportbot.models import ShopDailyReport
from reportbot.services import (
    create_or_update_daily_report,
    get_active_shops_for_day,
)
from reportbot.telegram_service import send_photo


class Command(BaseCommand):
    help = "Send daily shop PNG reports to DS Express team Telegram group"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default="")

    def handle(self, *args, **options):
        if options["date"]:
            report_date = timezone.datetime.strptime(options["date"], "%Y-%m-%d").date()
        else:
            report_date = timezone.localdate()

        shop_ids = list(get_active_shops_for_day(report_date))
        self.stdout.write(f"Active shops with done >= 1: {len(shop_ids)}")

        for shop in Seller.objects.filter(id__in=shop_ids).order_by("name"):
            report, data = create_or_update_daily_report(shop, report_date)

            caption = (
                f"📦 Delivery Report\n\n"
                f"Report ID: {report.report_code}\n"
                f"Shop: {shop.name}\n"
                f"Date: {report_date}\n\n"
                f"Status: WAITING_CHECK\n\n"
                f"React to update status:\n"
                f"✅ Approve\n"
                f"⚠️ Need Fix\n"
                f"⏸️ Hold"
            )

            resp = send_photo(
                settings.TELEGRAM_DS_TEAM_CHAT_ID,
                report.png_path,
                caption=caption,
            )

            result = resp.get("result") or {}
            report.telegram_chat_id = str(result.get("chat", {}).get("id", ""))
            report.telegram_message_id = str(result.get("message_id", ""))
            report.save(update_fields=["telegram_chat_id", "telegram_message_id", "updated_at"])

            self.stdout.write(self.style.SUCCESS(f"Sent {report.report_code}"))