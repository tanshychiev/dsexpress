import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ContextTypes

BOT_TOKEN = "8672231505:AAHNxZcrFWmob6nD8fiKyCzTAn-Gk68Vz9s"
DJANGO_API_URL = "http://127.0.0.1:8000/portal/telegram/update-booking/"


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    print("BUTTON CLICKED:", data)
    print("FROM USER OBJECT:", query.from_user)

    try:
        action, booking_id = data.split(":")
    except ValueError:
        await query.answer("Invalid data", show_alert=True)
        return

    telegram_user_id = ""
    telegram_name = ""
    telegram_username = ""

    if query.from_user:
        telegram_user_id = str(query.from_user.id or "")
        telegram_name = (query.from_user.full_name or "").strip()
        telegram_username = (query.from_user.username or "").strip()

    payload = {
        "booking_id": booking_id,
        "action": action,
        "telegram_user_id": telegram_user_id,
        "telegram_name": telegram_name,
        "telegram_username": telegram_username,
    }

    print("POST PAYLOAD:", payload)

    try:
        response = requests.post(
            DJANGO_API_URL,
            json=payload,
            timeout=10,
        )

        print("STATUS CODE:", response.status_code)
        print("RESPONSE TEXT:", response.text)

        result = response.json()

        if result.get("success"):
            await query.answer("Updated successfully")
        else:
            await query.answer(result.get("error", "Update failed"), show_alert=True)

    except Exception as e:
        print("Polling bot error:", e)
        try:
            await query.answer("System error", show_alert=True)
        except Exception:
            pass


def main():
    print("BOT VERSION: WITH USER INFO")
    print("USING URL:", DJANGO_API_URL)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_button))

    print("Telegram polling bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()