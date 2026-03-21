import os
from pathlib import Path

import requests
from django.conf import settings


def telegram_api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"


def send_photo(chat_id: str, photo_path: str, caption: str = "") -> dict:
    path = Path(photo_path)
    if not path.exists():
        raise FileNotFoundError(f"Photo not found: {photo_path}")

    with open(path, "rb") as f:
        resp = requests.post(
            telegram_api_url("sendPhoto"),
            data={
                "chat_id": chat_id,
                "caption": caption,
            },
            files={"photo": f},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json()


def send_message(chat_id: str, text: str) -> dict:
    resp = requests.post(
        telegram_api_url("sendMessage"),
        data={
            "chat_id": chat_id,
            "text": text,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()