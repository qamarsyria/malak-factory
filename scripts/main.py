"""المنسّق: من المحتوى إلى النشر على 3 منصات."""
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
from config import SITE_URL


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
    print(f"📝 النص الأصلي: {item['text'][:120]}...")

    script = reformulate(item)
    print(f"✍️  السؤال: {script['question'] or '(بدون)'}")
    print(f"✍️  الجواب: {script['answer'][:120]}...")
    print(f"🔁 استُخدم النص الأصلي؟ {script['used_original']}")

    script["attribution"] = item.get("attribution", "")
    check = verify(script)
    print(f"🔎 التحقق: ok={check['ok']}, تحذيرات={len(check['warnings'])}")
    for w in check["warnings"][:3]:
        print(f"   ⚠️  {w}")

    if not check["ok"]:
        print("❌ النص لم يجتز التحقق — إيقاف بدون نشر")
        sys.exit(1)

    # الصوت: إذا لا يوجد سؤال، انطق الجواب فقط
    if script["question"]:
        voice_text = script["question"] + ". " + script["answer"]
    else:
        voice_text = script["answer"]
    voice_path = generate(voice_text, "out/voice.mp3")

    video_path = build(script, voice_path, "out/video.mp4")

    caption = build_caption(item, script)
    title = script["question"][:95] if script["question"] else script["answer"][:95]

    results = {}

    try:
        mid = telegram.upload(video_path, caption)
        results["telegram"] = f"✅ msg_id={mid}"
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
    else:
        print("⚠️  لم يتم تحديث الحالة")
        sys.exit(1)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
