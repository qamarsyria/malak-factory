"""رفع فيديو إلى قناة تيليجرام."""
import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, HASHTAGS


def upload(video_path, caption):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN أو TELEGRAM_CHAT_ID غير موجود")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
    full_caption = f"{caption}\n\n{HASHTAGS}"[:1024]

    with open(video_path, "rb") as f:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": full_caption,
                "supports_streaming": True,
            },
            files={"video": f},
            timeout=180,
        )

    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram فشل: {data}")
    return data["result"]["message_id"]
