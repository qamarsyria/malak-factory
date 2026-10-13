"""رفع Reel إلى فيسبوك عبر Graph API."""
import requests
from pathlib import Path
from config import FACEBOOK_PAGE_TOKEN, FACEBOOK_PAGE_ID, HASHTAGS

GRAPH = "https://graph.facebook.com/v21.0"


def upload(video_path, caption):
    if not FACEBOOK_PAGE_TOKEN or not FACEBOOK_PAGE_ID:
        raise RuntimeError("FACEBOOK_PAGE_TOKEN أو FACEBOOK_PAGE_ID غير موجود")

    size = Path(video_path).stat().st_size

    # 1) بدء الجلسة
    r = requests.post(
        f"{GRAPH}/{FACEBOOK_PAGE_ID}/video_reels",
        data={
            "upload_phase": "start",
            "access_token": FACEBOOK_PAGE_TOKEN,
        },
        timeout=60,
    )
    data = r.json()
    if "video_id" not in data or "upload_url" not in data:
        raise RuntimeError(f"FB start فشل: {data}")

    video_id = data["video_id"]
    upload_url = data["upload_url"]

    # 2) الرفع
    with open(video_path, "rb") as f:
        headers = {
            "Authorization": f"OAuth {FACEBOOK_PAGE_TOKEN}",
            "offset": "0",
            "file_size": str(size),
        }
        r2 = requests.post(upload_url, headers=headers, data=f, timeout=600)
    if r2.status_code >= 400:
        raise RuntimeError(f"FB upload فشل: {r2.status_code} {r2.text[:300]}")

    # 3) النشر
    full_caption = f"{caption}\n\n{HASHTAGS}"
    r3 = requests.post(
        f"{GRAPH}/{FACEBOOK_PAGE_ID}/video_reels",
        data={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": full_caption,
            "access_token": FACEBOOK_PAGE_TOKEN,
        },
        timeout=60,
    )
    data3 = r3.json()
    if "success" not in data3:
        raise RuntimeError(f"FB publish فشل: {data3}")
    return video_id
