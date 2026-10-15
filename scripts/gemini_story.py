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

المهمة: حوّل قصة يوسف عليه السلام (من سورة يوسف) إلى حلقات صوتية درامية، حلقاتها 12-15 دقيقة.

المصادر (فقط هذه):
- الآيات من سورة يوسف (بالرسم العثماني)
- التفسير الميسّر لكل آية

قواعد صارمة:
1. لا تُضف أي معلومة غير موجودة في الآيات أو التفسير الميسّر.
2. الآيات تُحفظ حرفياً.
3. لا تُصوّر الأنبياء بأي شكل.
4. لا تذكر مذاهب فقهية ولا فرق إسلامية.
5. لا تذكر أسماء علماء معاصرين.

============================================================
ترتيب المقاطع:
============================================================
- المقطع الأول: "intro" (نص مقدمة تعريفي، 15-20 كلمة)
- ثم لكل آية:
  * "quran" (الآية كاملة)
  * "narration" (تفسير موسّع، 8-10 جمل، 60-90 ثانية)
- "outro" (نص خاتمة، 15-20 كلمة)

============================================================
قواعد السرد (narration):
============================================================
- الطول: 8-10 جمل لكل آية (لا أقل).
- الأسلوب: فصحى سردية، درامية، متدفقة.
- ابدأ بـ: "يقول الله تعالى..."، "وهنا..."، "ثم..."، "وفي هذه اللحظة..."، "فإذا..."، "ولكن..."، "وهكذا...".
- إثراء المحتوى: وسّع المعنى بأسلوب أدبي (لا إضافة معلومات جديدة).
- استخدم: "أنعم الله"، "تبارك الله"، "تتجلى"، "تتكشف"، "تتراءى".

============================================================
الموسيقى (music):
============================================================
- "music": "mood" (string)، و "intensity": 0.20-0.30 (رقم منفصل)
- الحالات: calm, mystical, tense, sad, epic, joyful, reflective, fearful, peaceful, dark, hope, majestic
- الآيات: "music": null

============================================================
المؤثرات (sfx):
============================================================
- أصوات بيئية: wind-soft, night-wind, water-drip, thunder-distant, rain-soft, sea-waves, sand-storm, footsteps, door-wood, fire-crackle
- الآيات: "sfx": []

============================================================
الهمسات (whispers) — اختياري:
============================================================
- تستخدم في: مشهد مؤامرة جماعية، وسوسة شيطانية، تفكير جماعي سلبي
- عدد: 6-10 همسات
- كل همسة: {"voice": "Charon|Puck|Fenrir|Orus", "text": "...", "delay": ثواني, "volume": 0.13-0.18}

============================================================
دقات القلب (heartbeat) — اختياري:
============================================================
- تستخدم في: خوف، خطر، ترقّب، صدمة، مطاردة
- {"start_bpm": 60-90, "end_bpm": 90-160, "duration": 8-20, "volume_start": 0.10, "volume_end": 0.20}

============================================================
الخلفيات البصرية (visual):
============================================================
- "type": "image" أو "video"
- "query": كلمة إنجليزية دقيقة (desert, night-sky, sea-waves, well-water, ancient-scroll, ...)
- استخدم "video" للأحداث الكبرى فقط.

============================================================
المدة الإجمالية: 12-15 دقيقة.
============================================================

أعد JSON فقط:
{
  "episode_number": 1,
  "title": "عنوان",
  "total_duration_estimate": 900,
  "segments": [...]
}"""

============================================================
ترتيب المقاطع (البنية الدرامية):
============================================================

- المقطع الأول: "intro" (10-15 ثانية)
- ثم لكل آية بالترتيب:
  * مقطع من نوع "quran" (الآية كاملة)
  * مقطع من نوع "narration" (تفسيرها + سردك الدرامي، 3-5 جمل)
- المقطع الأخير: "outro" (10-15 ثانية، ينتهي بـ "يتبع في الحلقة الثانية إن شاء الله" إذا للقصة تكملة)

مثال البنية (لـ 3 آيات):
[
  {type: "intro"},
  {type: "quran", ayah_numbers: [1]},
  {type: "narration", ...},
  {type: "quran", ayah_numbers: [2]},
  {type: "narration", ...},
  {type: "quran", ayah_numbers: [3]},
  {type: "narration", ...},
  {type: "outro"}
]

============================================================
قواعد السرد (narration):
============================================================
- الطول: 3-5 جمل لكل آية (لا أقل).
- الأسلوب: فصحى سردية، درامية، مؤثرة، متدفقة.
- استخدم: "ثم"، "وهكذا"، "وفي هذه اللحظة"، "فإذا"، "ولكن"، "وبينما".
- إثراء السرد: اشرح معاني التفسير، وسّعها بأسلوب أدبي (دون إضافة معلومة جديدة).
- ابدأ كل مقطع narration بعبارة انتقالية مناسبة (مثل: "يقول الله تعالى مبيناً..."، "وهنا يخبرنا التفسير..."، "ثم يستمر السياق القرآني...").

============================================================
الموسيقى (music):
============================================================
- تُحدد لكل مقطع narration / intro / outro.
- الحالات المتاحة: calm, mystical, tense, sad, epic, joyful, reflective, fearful, peaceful, dark, hope, majestic
- الشدة (intensity): 0.20-0.30
- الآيات: music: null (صامتة تماماً).

============================================================
المؤثرات (sfx):
============================================================
- أصوات بيئية فقط: wind-soft, night-wind, water-drip, thunder-distant, rain-soft, sea-waves, sand-storm, footsteps, door-wood, fire-crackle
- الآيات: sfx: [] (لا مؤثرات).

============================================================
الهمسات (whispers) — اختياري:
============================================================
تُستخدم فقط عند:
- مشهد مؤامرة جماعية (مثل: إخوة يوسف يتآمرون في الآيات 8-10)
- وسوسة شيطانية
- تفكير جماعي سلبي

كل همسة:
{
  "voice": "Charon|Puck|Fenrir|Orus",
  "text": "نص الهمس",
  "delay": ثواني (0.0, 0.5, 1.0, ...),
  "volume": 0.13-0.18
}

شروط:
- عدد الهمسات: 6-10
- كل همسة: 1.5-2.5 ثانية
- التداخل: 0.5-1.0 ثانية بين كل همسة والتي تليها
- النصوص من الآيات أو التفسير فقط (لا اختراع)
- المجموع: 8-15 ثانية من الهمس الجماعي

============================================================
دقات القلب (heartbeat) — اختياري:
============================================================
تُستخدم فقط عند:
- خوف شديد
- خطر محدق
- ترقّب
- صدمة
- مطاردة

الشكل:
{
  "start_bpm": 60-90,
  "end_bpm": 90-160,
  "duration": 8-20,
  "volume_start": 0.10,
  "volume_end": 0.20
}

============================================================
الخلفيات البصرية (visual):
============================================================
- النوع: "image" أو "video"
- الكلمة المفتاحية (query): بالإنجليزية، دقيقة، مناسبة للمشهد

الاقتراحات:
- صحراء → desert, sand-dunes
- ليل → night-sky, moon-night
- بحر → sea-waves, ocean-storm
- بئر → well-water, ancient-well
- نجوم → stars-night, galaxy
- نور → sunrise, light-rays
- مسجد → mosque-architecture
- قرآن → quran-text, ancient-manuscript
- غروب → sunset-desert
- رعد → storm-clouds, thunder
- مطر → rain-heavy
- قصر → ancient-palace

استخدم "video" فقط عند الأحداث الكبرى (غرق، طوفان، معركة).

============================================================
المدة الإجمالية لكل حلقة: 12-15 دقيقة.
============================================================

أعد النتيجة بصيغة JSON فقط:
{
  "episode_number": 1,
  "title": "عنوان الحلقة (اقترحه من محتوى الآيات)",
  "total_duration_estimate": 900,
  "segments": [...]
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
