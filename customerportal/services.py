import logging

logger = logging.getLogger(__name__)


def send_public_booking_to_telegram(cleaned_data):
    """
    Placeholder for Telegram sending.
    Later connect your Telegram bot here.
    """
    message = (
        "📦 New Public Booking Request\n"
        f"Name: {cleaned_data.get('name')}\n"
        f"Phone: {cleaned_data.get('phone')}\n"
        f"Pickup Location: {cleaned_data.get('pickup_location')}\n"
        f"Total PC: {cleaned_data.get('total_pc')}"
    )
    logger.info(message)
    return True