"""المنسّق: من المحتوى إلى النشر على 3 منصات."""
import sys
import traceback
import requests
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from content import pick_next, mark_published
from gemini import reformulate
from verifier import verify
from voice import generate
from video import build
from uploaders import telegram, facebook, youtube
from config import SITE_URL, VOICE_MAP

# استيراد السكريبت الذي يرسل الفيديو للمنصة
from update_feed import add_video_to_feed


def upload_to_catbox(file_path):
    """رفع الفيديو إلى tmpfiles.org للحصول على رابط MP4 مباشر"""
    try:
        print("☁️ جاري رفع الفيديو إلى tmpfiles.org...")
        with open(file_path, 'rb') as f:
            r = requests.post(
                'https://tmpfiles.org/api/v1/upload',
                files={'file': f},
                timeout=300
            )
        if r.status_code == 200:
            data = r.json()
            # الرابط يعود بصيغة https://tmpfiles.org/12345/name.mp4
            # نحتاج تحويله إلى رابط مباشر: https://tmpfiles.org/dl/12345/name.mp4
            raw_url = data['data']['url']
            direct_url = raw_url.replace('https://tmpfiles.org/', 'https://tmpfiles.org/dl/')
            print(f"✅ تم الرفع بنجاح: {direct_url}")
            return direct_url
        else:
            print(f"❌ فشل الرفع: {r.status_code} - {r.text}")
            return ""
    except Exception as e:
        print(f"❌ خطأ أثناء الرفع: {e}")
        return ""


def build_caption(item, script):
    q = script["question"].strip()
    a = script["answer"].strip()
    attr = item.get("attribution", "").strip()
    parts = []
    if q:
        parts += [q, ""]
    parts.append(a)
    if attr:
        parts += ["", f"📚 {attr}"]
    parts += ["", f"🌐 {SITE_URL}"]
    return "\n".join(parts)[:950]


def run():
    print(f"\n🕒 بدء التشغيل: {datetime.now(timezone.utc).isoformat()}")

    item, state = pick_next()
    print(f"📖 العنصر: {item['id']} | نوع={item['source_type']}")
    
    voice_name = item.get("voice") or VOICE_MAP.get(item["source_type"], "Charon")
    
    if item["source_type"] == "ruqyah":
        script = {
            "question": item.get("question", ""),
            "answer": item["text"],
            "used_original": True,
            "voice": voice_name,
        }
    else:
        script = reformulate(item)
        script["voice"] = voice_name

    script["attribution"] = item.get("attribution", "")
    check = verify(script)
    print(f"🔎 التحقق: ok={check['ok']}, تحذيرات={len(check['warnings'])}")

    if not check["ok"]:
        print("❌ النص لم يجتز التحقق — إيقاف")
        sys.exit(1)

    voice_text = script["answer"] if not script["question"] else f"{script['question']}. {script['answer']}"
    voice_path = generate(voice_text, "out/voice.mp3", voice=voice_name)

    video_path = build(script, voice_path, "out/video.mp4")

    caption = build_caption(item, script)
    title = script["question"][:95] if script["question"] else script["answer"][:95]

    results = {}

    # 1. النشر على تلغرام
    try:
        mid = telegram.upload(video_path, caption)
        results["telegram"] = f"✅ msg_id={mid}"
        
        # 2. الرفع على tmpfiles (للحصول على رابط للمنصة)
        video_url = upload_to_catbox(video_path)
        
        # 3. تحديث المنصة (feed)
        if video_url:
            add_video_to_feed(
                title=title,
                description=caption,
                video_url=video_url,
                category=item.get("source_type", "عام")
            )
            print("📱 تمت إضافة الفيديو إلى واجهة المنصة بنجاح!")
            
    except Exception as e:
        results["telegram"] = f"❌ {e}"

    # النشر على فيسبوك (إذا كان يعمل)
    try:
        vid = facebook.upload(video_path, caption)
        results["facebook"] = f"✅ video_id={vid}"
    except Exception as e:
        results["facebook"] = f"❌ {e}"

    # النشر على يوتيوب (إذا كان يعمل)
    try:
        yid = youtube.upload(video_path, title=title, description=caption)
        results["youtube"] = f"✅ id={yid} (خاص)"
    except Exception as e:
        results["youtube"] = f"❌ {e}"

    print("\n" + "=" * 50)
    print("التقرير النهائي")
    print("=" * 50)
    for k, v in results.items():
        print(f"  {k}: {v}")

    if results.get("telegram", "").startswith("✅"):
        mark_published(item, state)
        print("💾 تم تحديث الحالة")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
