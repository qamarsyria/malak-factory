"""
generate_story.py — المرحلة 3: التوليد
يقرأ audited/events/ ويُنتج حلقات (5 دقائق = ~700 كلمة + 3-4 آيات):
  1. يكتشف الأحداث الضخمة → LLM يقسمها لحلقات (يُحفظ في split.json)
  2. لكل حلقة:
     - LLM يحوّل السرد لسكريبت قصصي (بلا حشو، بلا نص قرآني)
     - LLM يكتب خلاصة (3 أسطر)
     - LLM يكتب تشويق (جملة واحدة)
     - CTA من القوالب
  3. يحفظ: generated/{story}-{ep}.json
  4. يُحدّث state.json + global.json

الاستخدام:
    python generate_story.py
"""

import os, re, sys, json, time, random
from pathlib import Path
from datetime import datetime, timezone
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker, notify_telegram
from filters_loader import get_llm_forbidden_rules
from outro_templates import OUTRO_TEMPLATES

# ═══════════════════════════════════════════════════════════
# المسارات
# ═══════════════════════════════════════════════════════════
STORIES_JSON   = _REPO_ROOT / "data" / "library" / "stories.json"
GLOBAL_STATE   = _REPO_ROOT / "state" / "global.json"
STORIES_DIR    = _REPO_ROOT / "data" / "stories"
OUTPUT_DIR     = STORIES_DIR  # generated/ داخل مجلد القصة

# ═══════════════════════════════════════════════════════════
# إعدادات
# ═══════════════════════════════════════════════════════════
TARGET_WORDS_MIN   = 600        # حد أدنى لكلمات السرد في الحلقة
TARGET_WORDS_MAX   = 800        # حد أعلى
AYAHS_PER_EPISODE  = (3, 5)     # نطاق معقول (min, max)
SPLIT_THRESHOLD_AYAHS  = 6      # > 6 آيات → حدث ضخم
SPLIT_THRESHOLD_WORDS  = 1000   # > 1000 كلمة سرد → حدث ضخم

# ═══════════════════════════════════════════════════════════
# مساعدات: تحويل الأرقام لكلمات عربية
# ═══════════════════════════════════════════════════════════
_ARABIC_UNITS = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
                 "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_ARABIC_TENS = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]


def num_to_arabic(n: int) -> str:
    """يحول 0-9999 لكلمات عربية."""
    if n == 0:
        return "صفر"
    if n < 10:
        return _ARABIC_UNITS[n]
    if n < 20:
        return _ARABIC_TEENS[n - 10]
    if n < 100:
        tens, units = divmod(n, 10)
        return _ARABIC_TENS[tens] if units == 0 else f"{_ARABIC_UNITS[units]} و{_ARABIC_TENS[tens]}"
    if n < 1000:
        h, rem = divmod(n, 100)
        prefix = _ARABIC_HUNDREDS[h]
        return prefix if rem == 0 else f"{prefix} و{num_to_arabic(rem)}"
    th, rem = divmod(n, 1000)
    prefix = f"{num_to_arabic(th)} ألف" if th > 1 else "ألف"
    return prefix if rem == 0 else f"{prefix} و{num_to_arabic(rem)}"


def arabize_numbers(text: str) -> str:
    """يستبدل كل الأرقام المستقلة بكلماتها العربية."""
    def _replace(match):
        return num_to_arabic(int(match.group(0)))
    return re.sub(r'\b(\d{1,4})\b', _replace, text)


def clean_narration(text: str) -> str:
    """تنظيف السرد: أرقام عربية، حذف إنجليزي ورموز."""
    if not text:
        return ""
    text = arabize_numbers(text)
    text = re.sub(r'[a-zA-Z]+', '', text)
    text = re.sub(r'[\[\]\(\)\{\}<>]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ═══════════════════════════════════════════════════════════
# استدعاء LLM
# ═══════════════════════════════════════════════════════════
def call_llm(prompt: str, is_json: bool = True, timeout: int = 180, max_tokens: int = 8192):
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:5]
    if not models:
        return None

    for model in models:
        if breaker.is_open(f"model:{model}"):
            continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens}
                }
                if is_json:
                    body["generationConfig"]["responseMimeType"] = "application/json"

                r = requests.post(url, json=body, timeout=timeout)

                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts:
                        break
                    text = parts[0].get("text", "")
                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s:
                            break
                        text = text[s:e+1]
                    try:
                        parsed = json.loads(text) if is_json else text
                        pool._advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError:
                        break
                elif r.status_code == 429:
                    pool.mark_failed(3600)
                elif r.status_code in (404, 503):
                    breaker.record_failure(f"model:{model}")
                    break
            except requests.exceptions.Timeout:
                pass
            except Exception:
                pass
    return None


# ═══════════════════════════════════════════════════════════
# 1. تقسيم الأحداث الضخمة
# ═══════════════════════════════════════════════════════════
def llm_split_event(event_id: str, event_title: str, ayahs: list, narrative: list) -> dict:
    """
    يطلب من LLM تقسيم حدث ضخم لحلقات متعددة.
    يعيد: {"episodes": [{"sub_title", "ayah_refs", "narrative_indices"}], "reason": "..."}
    """
    ayah_index = "\n".join([
        f"[{i}] {a['surah_name']} {a['ref']}: {a['text'][:150]}"
        for i, a in enumerate(ayahs)
    ])
    narrative_index = "\n".join([
        f"[{i}] ({b.get('source','')}) {b.get('text','')[:250]}..."
        for i, b in enumerate(narrative)
    ])

    prompt = f"""أنت مؤرخ قصصي. حلقة القصة المستهدفة: ~5 دقائق (700 كلمة + 3-4 آيات).

الحدث الحالي: "{event_title}" — كبير جداً ويحتاج تقسيم لحلقات.

الآيات المتاحة:
{ayah_index}

السرد المتاح:
{narrative_index}

قسّم هذا الحدث لحلقات بحيث:
- كل حلقة: 3-4 آيات + ~700 كلمة سرد.
- احترم التسلسل الزمني (لا تقفز).
- كل آية من الآيات المذكورة يجب أن تظهر في حلقة واحدة فقط.
- كل narrative index يجب أن يظهر في حلقة واحدة فقط.
- عدد الحلقات: 2 إلى 4 (حسب الحجم).

أعد JSON فقط:
{{
  "episodes": [
    {{
      "sub_title": "عنوان مختصر للحلقة",
      "ayah_refs": ["12:4", "12:5"],
      "narrative_indices": [0, 1, 2, 3]
    }}
  ],
  "reason": "شرح مختصر"
}}"""

    result = call_llm(prompt, is_json=True, timeout=180, max_tokens=4096)
    if result and isinstance(result, dict) and result.get("episodes"):
        return result
    # fallback: 2 حلقات متساوية
    half = len(ayahs) // 2
    return {
        "episodes": [
            {"sub_title": "الجزء الأول", "ayah_refs": [a["ref"] for a in ayahs[:half]],
             "narrative_indices": list(range(len(narrative) // 2))},
            {"sub_title": "الجزء الثاني", "ayah_refs": [a["ref"] for a in ayahs[half:]],
             "narrative_indices": list(range(len(narrative) // 2, len(narrative)))},
        ],
        "reason": "fallback: تقسيم متساوٍ"
    }


def ensure_splits(story_id: str):
    """يضمن وجود split.json لكل حدث (يُولّدها عند الحاجة)."""
    events_dir = STORIES_DIR / story_id / "audited" / "events"
    if not events_dir.exists():
        raise RuntimeError(f"لا توجد أحداث مدققة: {events_dir}")

    ev_dirs = sorted([d for d in events_dir.iterdir() if d.is_dir() and d.name.startswith("event_")])

    for ev_dir in ev_dirs:
        split_file = ev_dir / "split.json"
        if split_file.exists():
            continue

        ayahs = json.loads((ev_dir / "ayahs.json").read_text(encoding="utf-8"))
        narrative = json.loads((ev_dir / "narrative.json").read_text(encoding="utf-8"))

        total_words = sum(len(b.get("text", "").split()) for b in narrative)
        needs_split = len(ayahs) > SPLIT_THRESHOLD_AYAHS or total_words > SPLIT_THRESHOLD_WORDS

        if not needs_split:
            split_data = {
                "episodes": [{"sub_title": "", "ayah_refs": None, "narrative_indices": None}],
                "reason": f"small_event (ayahs={len(ayahs)}, words={total_words})"
            }
        else:
            print(f"   🔪 تقسيم {ev_dir.name} ({len(ayahs)} آية, {total_words} كلمة)")
            title = ev_dir.name.replace("event_", "الحدث ")
            split_data = llm_split_event(ev_dir.name, title, ayahs, narrative)

        split_file.write_text(json.dumps(split_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✅ {ev_dir.name}: {len(split_data['episodes'])} حلقة")

    return ev_dirs


# ═══════════════════════════════════════════════════════════
# 2. توليد حلقة واحدة
# ═══════════════════════════════════════════════════════════
SCRIPT_PROMPT = """أنت راوي قصص إسلامي محترف. مهمتك تحويل المادة التالية إلى سكريبت صوتي قصصي مشوق.

📖 القصة: {story_title}
📖 الحلقة: {ep_title}
📖 الحدث: {event_title}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الآيات المتاحة (يجب تضمينها كلها في السكريبت):
{ayahs_block}

السرد المتاح (المراجع):
{narrative_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 الشكل المطلوب:
سكريبت يتناوب بين:
- "narration": سرد قصصي مشوق (لا يحتوي نصاً قرآنياً حرفياً أبداً)
- "quran": إشارة لآية (نستخدم [آية: سورة:آية] كمرجع فقط)

⚠️ قواعد صارمة:

1️⃣ **الآيات لا تُكتب حرفياً في السرد أبداً**. إذا كان في السرد الأصلي نص قرآني، حوّله لشرح قصصي.
   مثال خاطئ: "قال تعالى: كلما دخل عليها زكريا المحراب"
   مثال صحيح: "كان زكريا عليه السلام كلما دخل على مريم في محرابها يجد عندها رزقاً عجيباً"

2️⃣ **السرد 600-800 كلمة** (لا أكثر، لا أقل). حلقة 5 دقائق.

3️⃣ **التوثيق 100%**: كل معلومة من السرد المتاح أو من الآيات. ممنوع الإسرائيليات.

4️⃣ **مكافحة الحشو**: يُمنع منعاً باتاً:
   - "في خضم الأحداث"، "لا شك أن"، "من المعلوم"، "تجدر الإشارة"
   - "كما نعلم جميعاً"، "في هذا السياق"، "خلاصة القول"
   - أي جملة لا تحمل معلومة جديدة أو حدثاً أو مشهداً

5️⃣ **ترتيب الآيات**: كل آية تأتي في لحظتها المناسبة من السرد (مرتبطة بحدثها).

6️⃣ **اللغة**: عربية فصحى. يُمنع أي حرف إنجليزي أو رقم (باستثناء مراجع الآيات [x:y]).

7️⃣ **احترام مطلق**: لا وصف لشكل الأنبياء/الملائكة/الصحابة، لا تشبيه للذات الإلهية.

{forbidden_rules}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

أعد JSON فقط:
{{
  "title": "عنوان الحلقة الرئيسي (3-6 كلمات)",
  "segments": [
    {{"type": "narration", "text": "..."}},
    {{"type": "quran", "surah": 12, "ayah_numbers": [4], "display": "يوسف: 4"}},
    {{"type": "narration", "text": "..."}},
    ...
  ]
}}

⚠️ يجب أن يحتوي السكريبت على {ayahs_expected} آية قرآنية على الأقل.
⚠️ السرد الكلي (كل مقاطع narration) يجب أن يكون بين 600 و 800 كلمة.
"""


def generate_episode_script(story_title: str, ep_title: str, event_title: str,
                            ayahs: list, narrative: list) -> dict | None:
    """يولّد سكريبت حلقة واحدة عبر LLM."""

    ayahs_block = "\n".join([
        f"[{a['ref']}] سورة {a['surah_name']}: {a['text']}"
        for a in ayahs
    ]) if ayahs else "(لا توجد آيات مرتبطة)"

    narrative_block = "\n\n".join([
        f"[{i}] (المصدر: {b.get('source', '')})\n{b.get('text', '')[:800]}"
        for i, b in enumerate(narrative)
    ]) if narrative else "(لا يوجد سرد إضافي)"

    prompt = SCRIPT_PROMPT.format(
        story_title=story_title,
        ep_title=ep_title,
        event_title=event_title,
        ayahs_block=ayahs_block,
        narrative_block=narrative_block,
        forbidden_rules=get_llm_forbidden_rules(),
        ayahs_expected=len(ayahs) if ayahs else 3,
    )

    result = call_llm(prompt, is_json=True, timeout=240, max_tokens=8192)
    if not result or not isinstance(result, dict) or "segments" not in result:
        return None
    return result


def validate_script(script: dict, expected_ayahs: list) -> tuple[bool, list[str]]:
    """يتحقق من جودة السكريبت."""
    issues = []
    segments = script.get("segments", [])

    if not segments:
        return False, ["no_segments"]

    # 1. التحقق من الآيات
    ayahs_in_script = [s for s in segments if s.get("type") == "quran"]
    expected_refs = {a["ref"] for a in expected_ayahs}
    actual_refs = set()
    for s in ayahs_in_script:
        surah = s.get("surah")
        for n in s.get("ayah_numbers", []):
            actual_refs.add(f"{surah}:{n}")

    missing = expected_refs - actual_refs
    if missing:
        issues.append(f"missing_ayahs: {sorted(missing)}")

    # 2. عدد الكلمات
    narration_words = sum(
        len(s.get("text", "").split())
        for s in segments if s.get("type") == "narration"
    )
    if narration_words < TARGET_WORDS_MIN:
        issues.append(f"too_short: {narration_words} < {TARGET_WORDS_MIN}")
    elif narration_words > TARGET_WORDS_MAX + 200:
        issues.append(f"too_long: {narration_words} > {TARGET_WORDS_MAX}")

    # 3. لا إنجليزي في narration
    for s in segments:
        if s.get("type") == "narration":
            if re.search(r'[a-zA-Z]', s.get("text", "")):
                issues.append("english_in_narration")
                break

    return (len(issues) == 0), issues


# ═══════════════════════════════════════════════════════════
# 3. الخلاصة والتشويق
# ═══════════════════════════════════════════════════════════
def generate_summary(story_title: str, ep_title: str, script_segments: list) -> str:
    """استدعاء منفصل لتوليد خلاصة (3 أسطر)."""
    narration_text = " ".join(
        s.get("text", "") for s in script_segments if s.get("type") == "narration"
    )[:3000]

    prompt = f"""القصة: {story_title} — {ep_title}
هذا سرد الحلقة:
{narration_text}

اكتب "خلاصة وعبرة" بليغة من 3 أسطر:
- لا تكرر السرد.
- عبرة عامة تستفيد منها.
- فصحى.
- بلا مقدمات ("وفي الختام" ممنوعة).

أعد النص فقط (3 أسطر)."""

    result = call_llm(prompt, is_json=False, timeout=60, max_tokens=300)
    return clean_narration(result) if isinstance(result, str) else ""


def generate_teaser(story_title: str, next_ep_title: str | None) -> str:
    """استدعاء منفصل لتوليد جملة تشويق."""
    next_hint = f"الحلقة القادمة: {next_ep_title}" if next_ep_title else "الحلقة القادمة"

    prompt = f"""القصة: {story_title}. {next_hint}.

اكتب جملة واحدة فقط تشوّق المستمع للحلقة القادمة.
- ابدأ بسؤال درامي (فكيف...؟ ماذا...؟ هل...؟).
- بلا مقدمات ("إليك"، "هذا"، "ترقبوا" ممنوعة).
- جملة واحدة فقط.

أعد الجملة فقط."""

    result = call_llm(prompt, is_json=False, timeout=60, max_tokens=150)
    teaser = clean_narration(result) if isinstance(result, str) else ""
    if len(teaser) < 20:
        teaser = "فماذا حدث بعد ذلك؟ وكيف انتهت هذه الرحلة؟"
    return teaser


# ═══════════════════════════════════════════════════════════
# 4. الحالة (state.json لكل قصة)
# ═══════════════════════════════════════════════════════════
def load_story_state(story_id: str) -> dict:
    path = STORIES_DIR / story_id / "state.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "story_id": story_id,
        "current_event_index": 0,
        "current_sub_episode": 0,
        "total_episodes_generated": 0,
        "completed": False,
        "last_generated": None,
    }


def save_story_state(story_id: str, state: dict):
    path = STORIES_DIR / story_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_generated"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_global_state() -> dict:
    return json.loads(GLOBAL_STATE.read_text(encoding="utf-8"))


def save_global_state(state: dict):
    GLOBAL_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════
# 5. الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("🎬 توليد حلقة جديدة")
    print("=" * 60)

    # 1. القصة الحالية
    gstate = load_global_state()
    story_id = gstate["current_story_id"]
    library = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة")
        sys.exit(1)

    print(f"📖 {story['title']}")

    # 2. تجهيز الـ splits
    print("\n🔪 تجهيز التقسيمات...")
    ev_dirs = ensure_splits(story_id)
    print(f"📦 {len(ev_dirs)} حدث")

    # 3. الحالة
    sstate = load_story_state(story_id)
    ev_idx = sstate["current_event_index"]
    sub_idx = sstate["current_sub_episode"]

    # 4. هل انتهت القصة؟
    if ev_idx >= len(ev_dirs):
        print(f"\n🎉 القصة '{story['title']}' مكتملة!")
        sstate["completed"] = True
        save_story_state(story_id, sstate)

        # انتقال للقصة التالية
        next_idx = gstate["current_story_index"] + 1
        if next_idx >= len(library["stories"]):
            print("🎊 الموسوعة كاملة! دورة جديدة")
            gstate["completed_stories"] = [s["id"] for s in library["stories"]]
            gstate["current_story_index"] = 0
            gstate["project_cycle"] += 1
            next_idx = 0

        gstate["current_story_id"] = library["stories"][next_idx]["id"]
        gstate["current_story_index"] = next_idx
        gstate["current_story_phase"] = "audited"  # ستُعاد الجمع لاحقاً
        save_global_state(gstate)
        notify_telegram(f"✅ اكتملت: {story['title']}\n🎯 التالي: {library['stories'][next_idx]['title']}", level="success")
        return

    # 5. الحدث الحالي
    ev_dir = ev_dirs[ev_idx]
    split = json.loads((ev_dir / "split.json").read_text(encoding="utf-8"))
    episodes_in_event = split["episodes"]

    if sub_idx >= len(episodes_in_event):
        # ننتقل للحدث التالي
        sstate["current_event_index"] += 1
        sstate["current_sub_episode"] = 0
        save_story_state(story_id, sstate)
        print(f"⏭️ انتقال للحدث التالي")
        return main()  # استدعاء recursively (آمن — مرة واحدة)

    # 6. تجهيز مادة الحلقة
    ep_spec = episodes_in_event[sub_idx]
    ayahs_all = json.loads((ev_dir / "ayahs.json").read_text(encoding="utf-8"))
    narrative_all = json.loads((ev_dir / "narrative.json").read_text(encoding="utf-8"))

    if ep_spec.get("ayah_refs") is None:
        ayahs_ep = ayahs_all
    else:
        ayahs_ep = [a for a in ayahs_all if a["ref"] in ep_spec["ayah_refs"]]

    if ep_spec.get("narrative_indices") is None:
        narrative_ep = narrative_all
    else:
        narrative_ep = [narrative_all[i] for i in ep_spec["narrative_indices"] if i < len(narrative_all)]

    ep_num = sstate["total_episodes_generated"] + 1
    ev_title = ep_spec.get("sub_title") or ev_dir.name
    print(f"\n🎬 حلقة {ep_num}: {ev_title}")
    print(f"   📖 {len(ayahs_ep)} آية | 📝 {len(narrative_ep)} block")

    # 7. توليد السكريبت (مع retry)
    script = None
    for attempt in range(3):
        print(f"   ✍️ محاولة {attempt+1}/3...")
        script = generate_episode_script(
            story_title=story["title"],
            ep_title=ev_title,
            event_title=ev_dir.name,
            ayahs=ayahs_ep,
            narrative=narrative_ep,
        )
        if script:
            ok, issues = validate_script(script, ayahs_ep)
            if ok:
                print(f"   ✅ سكريبت صالح")
                break
            else:
                print(f"   ⚠️ مشاكل: {issues}")
        time.sleep(3)

    if not script:
        print("❌ فشل توليد السكريبت بعد 3 محاولات")
        sys.exit(1)

    # 8. الخلاصة والتشويق
    print("   📝 خلاصة...")
    summary = generate_summary(story["title"], ev_title, script["segments"])

    # العنوان القادم
    next_sub = sub_idx + 1
    if next_sub < len(episodes_in_event):
        next_ep_title = episodes_in_event[next_sub].get("sub_title", "")
    elif ev_idx + 1 < len(ev_dirs):
        next_ev = ev_dirs[ev_idx + 1]
        next_split = json.loads((next_ev / "split.json").read_text(encoding="utf-8"))
        next_ep_title = next_split["episodes"][0].get("sub_title", "")
    else:
        next_ep_title = None

    print("   📢 تشويق...")
    teaser = generate_teaser(story["title"], next_ep_title)

    # 9. تجميع الحلقة النهائية
    segments = script["segments"]
    if summary:
        segments.append({"type": "summary", "text": summary, "visual": "sunrise sky"})
    if teaser:
        segments.append({
            "type": "teaser",
            "text": teaser,
            "next_episode_title": f"الحلقة {ep_num + 1}",
            "url": "https://qamarsyria.github.io/qamar-malak/",
            "platform_name": "ملاك المعرفة"
        })
    outro = random.choice(OUTRO_TEMPLATES)
    segments.append({"type": "cta", "text": outro["text"], "visual": "mosque architecture"})

    # تنظيف narration
    for s in segments:
        if s.get("type") == "narration":
            s["text"] = clean_narration(s["text"])

    # 10. الحفظ
    ep_data = {
        "episode_number": ep_num,
        "story_id": story_id,
        "story_title": story["title"],
        "title": f"{story['title']} — الحلقة {ep_num} — {script.get('title', ev_title)}",
        "segments": segments,
    }

    generated_dir = STORIES_DIR / story_id / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = generated_dir / f"{story_id}-{ep_num:02d}.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 حُفظ: {output_path}")

    # 11. تحديث الحالة
    sstate["current_sub_episode"] += 1
    sstate["total_episodes_generated"] = ep_num
    save_story_state(story_id, sstate)

    gstate["total_episodes_published"] = gstate.get("total_episodes_published", 0) + 1
    if gstate.get("current_era") != story["era"]:
        gstate["current_era"] = story["era"]
        gstate["current_era_episodes_published"] = 0
    gstate["current_era_episodes_published"] = gstate.get("current_era_episodes_published", 0) + 1
    save_global_state(gstate)

    notify_telegram(
        f"🎬 حلقة جديدة: {story['title']} — {ep_num}\n"
        f"📖 {script.get('title', ev_title)}",
        level="success"
    )
    print(f"\n🎉 اكتملت الحلقة {ep_num}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
