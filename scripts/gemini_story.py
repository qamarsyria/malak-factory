"""تقسيم القصة إلى حلقات باستخدام Gemini."""
import json
import os
import re
from pathlib import Path
import requests
from config import GEMINI_API_KEY

MODELS_TO_TRY = [
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]

# ============================================================
# البرومبت الأساسي
# ============================================================
PROMPT = """أنت كاتب سيناريو درامي إسلامي محترف.

المهمة: حوّل قصة يوسف عليه السلام (من سورة يوسف) إلى حلقات صوتية درامية.

المصادر:
- الآيات (111 آية من سورة يوسف)
- التفسير الميسّر لكل آية

قواعد صارمة:
1. لا تُضف أي معلومة غير موجودة في الآيات أو التفسير.
2. الآيات تُحفظ حرفياً (لا تعديل، لا قطع، لا اختصار).
3. لا تُذكر الأنبياء بأصواتهم، ولا تصفهم بصورة.
4. لا تذكر مذاهب فقهية ولا فرق إسلامية.

تنظيم الحلقة:
- كل حلقة تستمر 10-15 دقيقة.
- كل حلقة تبدأ بـ intro قصير (10-15 ثانية).
- ثم narration (السرد).
- ثم quran (الآيات الكاملة).
- تختتم بـ outro إذا لم تنته القصة، أو بنهاية نظيفة إذا انتهت.

أنواع المقاطع (segments):
- "intro": مقدمة الحلقة
- "narration": سرد (من التفسير بأسلوبك، دون إضافة معلومات)
- "quran": الآيات الكاملة (لا موسيقى، لا SFX، لا همسات، لا قطع)
- "outro": خاتمة الحلقة

لكل segment، حدّد:
- "type": النوع (intro/narration/quran/outro)
- "text": النص للقراءة
- "ayah_numbers": أرقام الآيات (فقط إذا كان quran)
- "music": {"mood": "الحالة", "intensity": 0.20-0.30} أو null للآيات
   - الحالات المتاحة: calm, mystical, tense, sad, epic, joyful,
     reflective, fearful, peaceful, dark, hope, majestic
- "sfx": قائمة أصوات بيئية ["wind-soft", "night-crickets", ...]
   - أصوات متاحة: wind-soft, night-wind, water-drip, thunder-distant,
     rain-soft, sea-waves, sand-storm, footsteps, door-wood, fire-crackle
- "whispers": قائمة همسات (اختياري)
   - تستخدم فقط عندما:
     * مشهد مؤامرة جماعية (إخوة يوسف يتآمرون)
     * وسوسة شيطانية
     * تفكير جماعي سلبي
   - كل همسة: {"voice": "Charon|Puck|Fenrir|Orus", "text": "...", "delay": ثواني, "volume": 0.13-0.18}
   - النصوص من الآيات أو التفسير فقط (لا اختراع)
   - عدد الهمسات: 5-10 همسات
   - مدة الهمس الجماعي: 8-15 ثانية
- "heartbeat": {"start_bpm": 60-90, "end_bpm": 90-160, "duration": 8-20, "volume_start": 0.10, "volume_end": 0.20}
   - يُستخدم فقط عند: خوف شديد، خطر محدق، ترقّب، صدمة، مطاردة
- "visual": {"type": "image|video", "query": "كلمة إنجليزية للبحث"}
   - الصور والفيديوهات من Pixabay
   - استخدم كلمات بحث دقيقة (desert, night-sky, sea-waves, well-water...)
   - إذا القصة عن الصحراء → desert
   - إذا القصة عن الليل → night-sky
   - إذا القصة عن البحر → sea-waves

أعد النتيجة بصيغة JSON فقط:
{
  "episode_number": 1,
  "title": "...",
  "total_duration_estimate": 900,
  "segments": [
    {
      "id": 1,
      "type": "intro",
      "text": "...",
      "music": {"mood": "majestic", "intensity": 0.25},
      "sfx": [],
      "visual": {"type": "image", "query": "desert night"}
    },
    ...
  ]
}"""


# ============================================================
# الدوال
# ============================================================
def _load_source(slug):
    """يقرأ source.json."""
    p = Path(f"data/stories/{slug}/source.json")
    if not p.exists():
        raise FileNotFoundError(f"لا يوجد ملف: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _build_prompt(source, episode_num, start_ayah, end_ayah):
    """يبني البرومبت مع الآيات المحددة للحلقة."""
    ayahs = source["ayahs"]
    selected = [a for a in ayahs if start_ayah <= a["number"] <= end_ayah]

    context = "\n\n".join([
        f"[آية {a['number']}]: {a['text'].strip()}\n"
        f"[تفسيرها]: {a['tafsir']}"
        for a in selected
    ])

    full_prompt = (
        f"{PROMPT}\n\n"
        f"---\n"
        f"الحلقة رقم: {episode_num}\n"
        f"الآيات المخصصة لهذه الحلقة: من {start_ayah} إلى {end_ayah}\n"
        f"---\n\n"
        f"النص الكامل للآيات والتفسير:\n\n{context}"
    )
    return full_prompt


def _call_gemini(model, prompt):
    """طلب إلى Gemini."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "topP": 0.9,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
    }
    try:
        r = requests.post(url, json=body, timeout=120)
        if r.status_code != 200:
            print(f"⚠️  {model}: HTTP {r.status_code} — {r.text[:200]}")
            return None
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            print(f"⚠️  {model}: لا يوجد candidates")
            return None
        parts = (cands[0].get("content") or {}).get("parts") or []
        if not parts:
            print(f"⚠️  {model}: لا يوجد parts")
            return None
        text = parts[0].get("text", "").strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"⚠️  {model}: JSON غير صالح — {e}")
        return None
    except Exception as e:
        print(f"⚠️  {model}: {e}")
        return None


def build_episode(slug, episode_num, start_ayah, end_ayah):
    """يبني حلقة واحدة."""
    print(f"\n🎬 بناء الحلقة {episode_num}: الآيات {start_ayah}-{end_ayah}")

    source = _load_source(slug)
    prompt = _build_prompt(source, episode_num, start_ayah, end_ayah)

    for model in MODELS_TO_TRY:
        print(f"🤖 محاولة {model}...")
        result = _call_gemini(model, prompt)
        if result and result.get("segments"):
            print(f"✅ نجح — {len(result['segments'])} مقطع")
            result["surah"] = source["surah"]
            result["surah_name"] = source["name"]
            result["episode_number"] = episode_num
            result["ayah_range"] = [start_ayah, end_ayah]
            return result

    raise RuntimeError("فشل كل النماذج")


def save_episode(slug, episode_data):
    """يحفظ الحلقة في ملف JSON."""
    ep_num = episode_data["episode_number"]
    out_dir = Path(f"data/stories/{slug}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"episode-{ep_num:02d}.json"
    out_file.write_text(
        json.dumps(episode_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"💾 {out_file}")
    return out_file


# ============================================================
# تقسيم السورة إلى حلقات
# ============================================================
# سورة يوسف — 111 آية
# التقسيم الدرامي المقترح:
YUSUF_EPISODES = [
    (1, 3),    # مقدمة السورة
    (4, 6),    # الرؤيا وبشارة يعقوب
    (7, 10),   # حسد الإخوة والمؤامرة
    (11, 14),  # طلب الإخوة وقلق يعقوب
    (15, 18),  # إلقاء يوسف في الجب والدم الكاذب
    (19, 22),  # التقاطه وبيعه وتمكينه في مصر
    (23, 29),  # مراودة امرأة العزيز
    (30, 35),  # نسوة المدينة والسجن
    (36, 42),  # دعوته في السجن
    (43, 49),  # رؤيا الملك وتفسيرها
    (50, 53),  # براءة يوسف
    (54, 57),  # تمكينه على خزائن مصر
    (58, 62),  # أول لقاء مع إخوته
    (63, 68),  # العودة إلى يعقوب
    (69, 76),  # بنيامين والسقاية
    (77, 82),  # اتهام بنيامين
    (83, 87),  # حزن يعقوب
    (88, 92),  # عودة الإخوة والتعارف
    (93, 98),  # القميص والبشارة
    (99, 101), # اللقاء الكبير والدعاء
    (102, 111),# الخاتمة والعبر
]


def main():
    slug = os.getenv("STORY_SLUG", "yusuf")
    ep_input = os.getenv("EPISODE_NUMBER", "")

    if ep_input:
        # بناء حلقة محددة
        ep_num = int(ep_input)
        if ep_num < 1 or ep_num > len(YUSUF_EPISODES):
            print(f"❌ رقم الحلقة خارج النطاق: 1-{len(YUSUF_EPISODES)}")
            return
        start, end = YUSUF_EPISODES[ep_num - 1]
        episode = build_episode(slug, ep_num, start, end)
        save_episode(slug, episode)
    else:
        # بناء كل الحلقات
        for ep_num, (start, end) in enumerate(YUSUF_EPISODES, 1):
            try:
                episode = build_episode(slug, ep_num, start, end)
                save_episode(slug, episode)
            except Exception as e:
                print(f"❌ فشل الحلقة {ep_num}: {e}")


if __name__ == "__main__":
    main()
