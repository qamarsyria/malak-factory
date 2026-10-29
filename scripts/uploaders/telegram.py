"""رفع فيديو إلى قناة تيليجرام — v2.
يضغط الفيديو تلقائياً إذا تجاوز 50 ميجابايت (حد تلغرام).
"""
import os
import subprocess
from pathlib import Path
import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, HASHTAGS


def upload(video_path, caption):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN أو TELEGRAM_CHAT_ID غير موجود")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
    full_caption = f"{caption}\n\n{HASHTAGS}"[:1024]

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"الفيديو غير موجود: {video_path}")

    size_mb = video_path.stat().st_size / (1024 * 1024)
    upload_path = video_path

    # ═══ الضغط التلقائي لتلغرام ═══
    if size_mb > 48:
        print(f"⚠️ حجم الفيديو {size_mb:.1f} ميجابايت (أكبر من حد تلغرام 50).")
        print("🗜️ جاري ضغط نسخة خاصة لتلغرام (720p)...", flush=True)
        
        compressed = video_path.with_name(video_path.stem + "_tg.mp4")
        
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", "scale=-2:1280",  # 720p لتقليل الحجم
            "-c:v", "libx264", "-crf", "32", "-preset", "fast",
            "-c:a", "aac", "-b:a", "96k",
            str(compressed)
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            c_size = compressed.stat().st_size / (1024 * 1024)
            print(f"✅ تم الضغط إلى {c_size:.1f} ميجابايت", flush=True)
            upload_path = compressed
        except Exception as e:
            print(f"⚠️ فشل ضغط الفيديو: {e}", flush=True)
            # في حال فشل الضغط، نحاول الرفع كما هو (قد يفشل)

    # ═══ الرفع ═══
    try:
        print(f"📤 رفع لتلغرام ({upload_path.stat().st_size / (1024*1024):.1f} ميجابايت)...", flush=True)
        with open(upload_path, "rb") as f:
            r = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": full_caption,
                    "supports_streaming": True,
                },
                files={"video": f},
                timeout=600,
            )

        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram فشل: {data}")
            
        print("✅ تم الرفع لتلغرام بنجاح", flush=True)
        return data["result"]["message_id"]
        
    finally:
        # ═══ تنظيف الملف المؤقت ═══
        if upload_path != video_path and upload_path.exists():
            upload_path.unlink()
            print("🧹 تم حذف النسخة المضغوطة المؤقتة", flush=True)
