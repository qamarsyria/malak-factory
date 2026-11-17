"""
generate_story.py — المرحلة 3: التوليد
يقرأ audited/events/ ويُنتج حلقات (5 دقائق = ~700 كلمة + 3-4 آيات).

⚠️ ملاحظة مهمة:
    هذا الملف لا يُحدّث الحالة (state) بعد التوليد!
    تحديث الحالة يتم في finalize_episode.py — فقط بعد نجاح الرفع على تلغرام.
    إذا فشل الرفع، الكاش يبقى، وإعادة التشغيل تستخدم الـ JSON الموجود.

⚠️ إدارة LATEST_EPISODE:
    - يُحذف في بداية التشغيل.
    - يُكتب في نهاية التوليد الناجح.
    - إذا كانت القصة مكتملة، لا يُكتب (workflow يتخطى البناء).

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
LATEST_EPISODE = _REPO_ROOT / "out" / "latest_episode.txt"

# ═══════════════════════════════════════════════════════════
# إعدادات
# ═══════════════════════════════════════════════════════════
TARGET_WORDS_MIN  = 600
TARGET_WORDS_MAX  = 800
SPLIT_THRESHOLD_AYAHS  = 6
SPLIT_THRESHOLD_WORDS  = 1000

# ═══════════════════════════════════════════════════════════
# الأرقام العربية
# ═══════════════════════════════════════════════════════════
_ARABIC_UNITS = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
                 "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_ARABIC_TENS = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]


def num_to_arabic(n: int) -> str:
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
    def _replace(m):
        return num_to_arabic(int(m.group(0)))
    return re.sub(r'\b(\d{1,4})\b', _replace, text)


def clean_narration(text: str) -> str:
    if not text:
        return ""
    text = arabize_numbers(text)
    text = re.sub(r'[a-zA-Z]+', '', text)
    text = re.sub(r'[\[\]\(\)\{\}<>]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ═══════════════════════════════════════════════════════════
# LLM
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
# تقسيم الأحداث
# ═══════════════════════════════════════════════════════════
def llm_split_event(event_id: str, event_title: str, ayahs: list, narrative: list) -> dict:
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
- احترم التسلسل الزمني.
- كل آية تظهر في حلقة واحدة فقط.
- كل narrative index يظهر في حلقة واحدة فقط.

أعد JSON فقط:
{{
  "episodes": [
    {{
      "sub_title": "عنوان مختصر",
      "ayah_refs": ["12:4"],
      "narrative_indices": [0, 1]
    }}
  ],
  "reason": "شرح مختصر"
}}"""

    result = call_llm(prompt, is_json=True, timeout=180, max_tokens=4096)
    if result and isinstance(result, dict) and result.get("episodes"):
        return result

    half = len(ayahs) // 2
    return {
        "episodes": [
            {"sub_title": "الجزء الأول", "ayah_refs": [a["ref"] for a in ayahs[:half]],
             "narrative_indices": list(range(len(narrative) // 2))},
            {"sub_title": "الجزء الثاني", "ayah_refs": [a["ref"] for a in ayahs[half:]],
             "narrative_indices": list(range(len(narrative) // 2, len(narrative)))},
        ],
        "reason": "fallback"
    }


def ensure_splits(story_id: str):
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
            split_data = llm_split_event(ev_dir.name, ev_dir.name.replace("event_", "الحدث "), ayahs, narrative)

        split_file.write_text(json.dumps(split_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✅ {ev_dir.name}: {len(split_data['episodes'])} حلقة")

    return ev_dirs


# ═══════════════════════════════════════════════════════════
# توليد سكريبت حلقة
# ═══════════════════════════════════════════════════════════
SCRIPT_PROMPT = """أنت راوي قصص إسلامي محترف. مهمتك تحويل المادة التالية إلى سكريبت صوتي قصصي مشوق.

📖 القصة: {story_title}
📖 الحلقة: {ep_title}
📖 الحدث: {event_title}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الآيات المتاحة (يجب تضمينها كلها):
{ayahs_block}

السرد المتاح (المراجع):
{narrative_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 الشكل المطلوب:
سكريبت يتناوب بين:
- "narration": سرد قصصي مشوق (لا يحتوي نصاً قرآنياً حرفياً)
- "quran": إشارة لآية (مرجع فقط [سورة:آية])

⚠️ قواعد صارمة:

1️⃣ **الآيات لا تُكتب حرفياً في السرد**. إذا كان النص الأصلي فيه آية، حوّلها لشرح قصصي.

2️⃣ **السرد 600-800 كلمة**.

3️⃣ **التوثيق 100%**. لا إسرائيليات.

4️⃣ **مكافحة الحشو**: يُمنع:
   - "في خضم الأحداث"، "لا شك أن"، "من المعلوم"
   - أي جملة لا تحمل معلومة جديدة

5️⃣ **ترتيب الآيات**: كل آية في لحظتها.

6️⃣ **اللغة**: عربية فصحى، بلا إنجليزي أو أرقام (عدا مراجع الآيات).

7️⃣ **احترام مطلق**: لا وصف لشكل الأنبياء/الملائكة/الصحابة، لا تشبيه للذات الإلهية.

{forbidden_rules}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

أعد JSON فقط:
{{
  "title": "عنوان الحلقة (3-6 كلمات)",
  "segments": [
    {{"type": "narration", "text": "..."}},
    {{"type": "quran", "surah": 12, "ayah_numbers": [4], "display": "يوسف: 4"}},
    ...
  ]
}}

⚠️ السرد الكلي بين 600 و 800 كلمة.
"""


def generate_episode_script(story_title, ep_title, event_title, ayahs, narrative):
    ayahs_block = "\n".join([
        f"[{a['ref']}] سورة {a['surah_name']}: {a['text']}"
        for a in ayahs
    ]) if ayahs else "(لا توجد آيات مرتبطة)"

    narrative_block = "\n\n".join([
        f"[{i}] (المصدر: {b.get('source', '')})\n{b.get('text', '')[:800]}"
        for i, b in enumerate(narrative)
    ]) if narrative else "(لا يوجد سرد)"

    prompt = SCRIPT_PROMPT.format(
        story_title=story_title,
        ep_title=ep_title,
        event_title=event_title,
        ayahs_block=ayahs_block,
        narrative_block=narrative_block,
        forbidden_rules=get_llm_forbidden_rules(),
    )

    return call_llm(prompt, is_json=True, timeout=240, max_tokens=8192)


def validate_script(script, expected_ayahs):
    issues = []
    segments = script.get("segments", [])
    if not segments:
        return False, ["no_segments"]

    expected_refs = {a["ref"] for a in expected_ayahs}
    actual_refs = set()
    for s in segments:
        if s.get("type") == "quran":
            surah = s.get("surah")
            for n in s.get("ayah_numbers", []):
                actual_refs.add(f"{surah}:{n}")
    if expected_refs - actual_refs:
        issues.append(f"missing_ayahs: {sorted(expected_refs - actual_refs)}")

    narration_words = sum(len(s.get("text", "").split())
                          for s in segments if s.get("type") == "narration")
    if narration_words < TARGET_WORDS_MIN:
        issues.append(f"too_short: {narration_words}")
    elif narration_words > TARGET_WORDS_MAX + 200:
        issues.append(f"too_long: {narration_words}")

    for s in segments:
        if s.get("type") == "narration" and re.search(r'[a-zA-Z]', s.get("text", "")):
            issues.append("english_in_narration")
            break

    return (len(issues) == 0), issues


# ═══════════════════════════════════════════════════════════
# الخلاصة والتشويق
# ═══════════════════════════════════════════════════════════
def generate_summary(story_title, ep_title, segments):
    narration_text = " ".join(
        s.get("text", "") for s in segments if s.get("type") == "narration"
    )[:3000]

    prompt = f"""القصة: {story_title} — {ep_title}
هذا سرد الحلقة:
{narration_text}

اكتب "خلاصة وعبرة" بليغة من 3 أسطر:
- لا تكرر السرد.
- عبرة عامة.
- فصحى.
- بلا مقدمات ("وفي الختام" ممنوعة).

أعد النص فقط (3 أسطر)."""

    result = call_llm(prompt, is_json=False, timeout=60, max_tokens=300)
    return clean_narration(result) if isinstance(result, str) else ""


def generate_teaser(story_title, next_ep_title):
    next_hint = f"الحلقة القادمة: {next_ep_title}" if next_ep_title else "الحلقة القادمة"

    prompt = f"""القصة: {story_title}. {next_hint}.

اكتب جملة واحدة فقط تشوّق المستمع للحلقة القادمة.
- ابدأ بسؤال درامي.
- بلا مقدمات.
- جملة واحدة فقط.

أعد الجملة فقط."""

    result = call_llm(prompt, is_json=False, timeout=60, max_tokens=150)
    teaser = clean_narration(result) if isinstance(result, str) else ""
    if len(teaser) < 20:
        teaser = "فماذا حدث بعد ذلك؟ وكيف انتهت هذه الرحلة؟"
    return teaser


# ═══════════════════════════════════════════════════════════
# الحالة
# ═══════════════════════════════════════════════════════════
def load_story_state(story_id):
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


def save_story_state(story_id, state):
    path = STORIES_DIR / story_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_generated"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_global_state():
    return json.loads(GLOBAL_STATE.read_text(encoding="utf-8"))


def save_global_state(state):
    GLOBAL_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def main(clear_latest: bool = True):
    if clear_latest:
        LATEST_EPISODE.parent.mkdir(parents=True, exist_ok=True)
        if LATEST_EPISODE.exists():
            LATEST_EPISODE.unlink()

    print("=" * 60)
    print("🎬 توليد حلقة جديدة")
    print("=" * 60)

    # 1. القصة
    gstate = load_global_state()
    story_id = gstate["current_story_id"]
    library = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة")
        sys.exit(1)

    print(f"📖 {story['title']}")

    # 2. splits
    print("\n🔪 تجهيز التقسيمات...")
    ev_dirs = ensure_splits(story_id)
    print(f"📦 {len(ev_dirs)} حدث")

    # 3. حالة القصة
    sstate = load_story_state(story_id)
    ev_idx = sstate["current_event_index"]
    sub_idx = sstate["current_sub_episode"]

    # 4. هل القصة مكتملة؟
    if ev_idx >= len(ev_dirs):
        print(f"\n🎉 القصة '{story['title']}' مكتملة!")
        sstate["completed"] = True
        save_story_state(story_id, sstate)

        next_idx = gstate["current_story_index"] + 1
        if next_idx >= len(library["stories"]):
            print("🎊 الموسوعة كاملة! دورة جديدة")
            gstate["completed_stories"] = [s["id"] for s in library["stories"]]
            gstate["current_story_index"] = 0
            gstate["project_cycle"] = gstate.get("project_cycle", 0) + 1
            next_idx = 0

        gstate["current_story_id"] = library["stories"][next_idx]["id"]
        gstate["current_story_index"] = next_idx
        gstate["current_story_phase"] = "audited"
        save_global_state(gstate)
        notify_telegram(
            f"✅ اكتملت: {story['title']}\n🎯 التالي: {library['stories'][next_idx]['title']}",
            level="success"
        )
        return

    # 5. الحدث
    ev_dir = ev_dirs[ev_idx]
    split = json.loads((ev_dir / "split.json").read_text(encoding="utf-8"))
    episodes_in_event = split["episodes"]

    if sub_idx >= len(episodes_in_event):
        sstate["current_event_index"] += 1
        sstate["current_sub_episode"] = 0
        save_story_state(story_id, sstate)
        print(f"⏭️ انتقال للحدث التالي")
        return main(clear_latest=False)

    # 6. تجهيز مادة الحلقة
    ep_spec = episodes_in_event[sub_idx]
    ayahs_all = json.loads((ev_dir / "ayahs.json").read_text(encoding="utf-8"))
    narrative_all = json.loads((ev_dir / "narrative.json").read_text(encoding="utf-8"))

    ayahs_ep = ayahs_all if ep_spec.get("ayah_refs") is None else [
        a for a in ayahs_all if a["ref"] in ep_spec["ayah_refs"]
    ]
    narrative_ep = narrative_all if ep_spec.get("narrative_indices") is None else [
        narrative_all[i] for i in ep_spec["narrative_indices"] if i < len(narrative_all)
    ]

    ep_num = sstate["total_episodes_generated"] + 1
    ev_title = ep_spec.get("sub_title") or ev_dir.name
    print(f"\n🎬 حلقة {ep_num}: {ev_title}")
    print(f"   📖 {len(ayahs_ep)} آية | 📝 {len(narrative_ep)} block")

    # 7. هل الحلقة موجودة سابقاً؟ (فشل رفع سابق)
    generated_dir = STORIES_DIR / story_id / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = generated_dir / f"{story_id}-{ep_num:02d}.json"

    if output_path.exists():
        print(f"\n♻️ الحلقة {ep_num} موجودة سابقاً (محاولة رفع سابقة فشلت)")
        print(f"   استخدام: {output_path}")
        LATEST_EPISODE.write_text(str(output_path), encoding="utf-8")
        notify_telegram(
            f"♻️ إعادة استخدام الحلقة {ep_num}: {story['title']}",
            level="info"
        )
        return

    # 8. التوليد
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
        print("❌ فشل التوليد")
        sys.exit(1)

    # 9. الخلاصة والتشويق
    print("   📝 خلاصة...")
    summary = generate_summary(story["title"], ev_title, script["segments"])

    next_sub = sub_idx + 1
    if next_sub < len(episodes_in_event):
        next_ep_title = episodes_in_event[next_sub].get("sub_title", "")
    elif ev_idx + 1 < len(ev_dirs):
        next_split = json.loads((ev_dirs[ev_idx + 1] / "split.json").read_text(encoding="utf-8"))
        next_ep_title = next_split["episodes"][0].get("sub_title", "")
    else:
        next_ep_title = None

    print("   📢 تشويق...")
    teaser = generate_teaser(story["title"], next_ep_title)

    # 10. تجميع
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

    for s in segments:
        if s.get("type") == "narration":
            s["text"] = clean_narration(s["text"])

    ep_data = {
        "episode_number": ep_num,
        "story_id": story_id,
        "story_title": story["title"],
        "title": f"{story['title']} — الحلقة {ep_num} — {script.get('title', ev_title)}",
        "segments": segments,
    }

    # 11. الحفظ
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 حُفظ: {output_path}")

    # 12. LATEST_EPISODE
    LATEST_EPISODE.write_text(str(output_path), encoding="utf-8")
    print(f"📌 out/latest_episode.txt ← {output_path.name}")

    # 13. إشعار (الحالة ستُحدَّث في finalize_episode.py — لا هنا!)
    notify_telegram(
        f"✍️ حلقة جاهزة للبناء: {story['title']} — {ep_num}\n"
        f"📖 {script.get('title', ev_title)}",
        level="info"
    )
    print(f"\n🎉 اكتملت الحلقة {ep_num} — بانتظار البناء والرفع")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
