"""المنسّق: من المحتوى إلى النشر."""
import sys
import traceback
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
from update_feed import add_video_to_feed, upload_video_to_github


def build_caption(item, script):
    q = script["question"].strip()
    a = script["answer"].strip()
    attr = item.get("attribution", "").strip()
    parts = []
    if q: parts += [q, ""]
    parts.append(a)
    if attr: parts += ["", f"📚 {attr}"]
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

    try:
        mid = telegram.upload(video_path, caption)
        results["telegram"] = f"✅ msg_id={mid}"
        
        # رفع الفيديو إلى GitHub
        filename = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        video_url = upload_video_to_github(video_path, filename)
        
        # إضافة الفيديو إلى ملف videos.json
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

    try:
        vid = facebook.upload(video_path, caption)
        results["facebook"] = f"✅ video_id={vid}"
    except Exception as e:
        results["facebook"] = f"❌ {e}"

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
