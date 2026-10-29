"""رفع فيديو إلى قناة تيليجرام — v3.
يضغط الفيديو تلقائياً إذا تجاوز 50 ميجابايت (حد تلغرام)، مع طباعة كل خطوة في السجل.
"""
import os
import subprocess
from pathlib import Path
import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, HASHTAGS


def upload(video_path, caption):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN أو TELEGRAM_CHAT_ID غير موجود")

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"الفيديو غير موجود: {video_path}")

    size_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"📤 [تلغرام] حجم الفيديو الأصلي: {size_mb:.1f} ميجابايت", flush=True)

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
    full_caption = f"{caption}\n\n{HASHTAGS}"[:1024]
    upload_path = video_path

    # ═══ إذا كان أكبر من 48 ميجا، نضغطه ═══
    if size_mb > 48:
        print(f"⚠️ [تلغرام] الفيديو أكبر من 48 ميجابايت. جاري إنشاء نسخة مضغوطة (720p)...", flush=True)
        
        compressed = video_path.with_name(video_path.stem + "_tg.mp4")
        
        # ضغط الفيديو
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", "scale=-2:1280", 
            "-c:v", "libx264", "-crf", "32", "-preset", "fast",
            "-c:a", "aac", "-b:a", "96k",
            str(compressed)
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            c_size = compressed.stat().st_size / (1024 * 1024)
            print(f"✅ [تلغرام] تم الضغط بنجاح: {c_size:.1f} ميجابايت", flush=True)
            
            if c_size > 49.5:
                print(f"❌ [تلغرام] حتى بعد الضغط، الحجم كبير جداً ({c_size:.1f}). سيتم التخطي.", flush=True)
                return None
                
            upload_path = compressed
        except Exception as e:
            print(f"❌ [تلغرام] فشل الضغط: {str(e)[:200]}", flush=True)
            print("⚠️ [تلغرام] سيتم محاولة الرفع بحجمه الأصلي (قد يفشل)...", flush=True)

    # ═══ الرفع ═══
    try:
        final_size = upload_path.stat().st_size / (1024 * 1024)
        print(f"🚀 [تلغرام] جاري الرفع ({final_size:.1f} ميجابايت)...", flush=True)
        
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
            raise RuntimeError(f"Telegram رفض الاستلام: {data}")
            
        print(f"✅ [تلغرام] تم الرفع بنجاح (msg_id={data['result']['message_id']})", flush=True)
        return data["result"]["message_id"]
        
    except Exception as e:
        print(f"❌ [تلغرام] فشل الرفع: {e}", flush=True)
        raise
    finally:
        # تنظيف الملف المؤقت
        if upload_path != video_path and upload_path.exists():
            upload_path.unlink()
            print("🧹 [تلغرام] تم حذف النسخة المضغوطة المؤقتة", flush=True)
