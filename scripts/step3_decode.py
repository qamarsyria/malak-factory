"""المخرج الذكي: يستخدم Gemini Flash لتحليل المشهد والنبرة، مع فلترة أمنية صارمة."""
import json
import re
import requests
from gemini_tts_pool import get_pool

# تعليمات صارمة (System Prompt) لا يمكن خرقها
DIRECTOR_PROMPT = """You are a STRICT Islamic cinematic director for a documentary about Quranic stories.

You will receive ONE sentence from the Arabic narration. Your job is to return a JSON object with:
1. "visual": 2-4 English words describing a background image for Pixabay.
2. "sfx": 1-3 English words describing a natural sound effect for Freesound. Leave empty if no sound is needed.
3. "voice_style": A short English hint (in parentheses) for the narrator's tone.

ABSOLUTE RULES (NEVER BREAK):
- NO humans, NO faces, NO women, NO men, NO children, NO prophets, NO companions, NO angels.
- Only: nature, architecture, objects, sky, deserts, mountains, water, fire, trees.
- NO music, NO instruments, NO songs. Only natural sounds: wind, rain, water, birds, thunder, fire, crickets.
- If the sentence is about a prophet, companion, or angel, DO NOT describe them. Describe only the environment or abstract concept.
- If you are unsure, use: visual="islamic architecture mosque", sfx="", voice_style="(in a calm, dignified tone)".

Emotion examples:
- Sadness: voice_style="(in a slow, sorrowful, tearful tone)"
- Fear: voice_style="(in a trembling, anxious, fearful tone)"
- Conspiracy: voice_style="(in a secretive, whispering, tense tone)"
- Dream/Prophecy: voice_style="(in a mysterious, dreamy, awe-filled tone)"
- Joy: voice_style="(in a hopeful, grateful, joyful tone)"
- Calm: voice_style="(in a calm, dignified tone)"

Return ONLY valid JSON (no markdown, no explanation):
@@@{"visual": "...", "sfx": "...", "voice_style": "..."}@@@

Sentence: @@@{text}@@@
"""

# كلمات محظورة (إذا ظهرت في رد Gemini، نستبدل الرد فوراً)
FORBIDDEN_VISUAL = [
    "woman", "women", "man", "men", "girl", "boy", "child", "face",
    "prophet", "messenger", "companion", "angel", "human", "person",
    "people", "crowd", "body", "hand", "eye", "hair", "skin",
]

FORBIDDEN_SFX = [
    "music", "song", "instrument", "piano", "guitar", "violin",
    "flute", "drum", "melody", "tune", "orchestra", "band",
]

# الاحتياطي الآمن (الكلمات المفتاحية)
SCENE_MAP = {
    "mecca": (["مكة", "الكعبة", "الحرم", "بيت الله"], "kaaba mecca ancient"),
    "egypt": (["مصر", "فرعون", "القصر", "العزيز", "الملك"], "ancient egypt palace"),
    "desert": (["صحراء", "رمل", "قفر", "بيداء", "كنعان"], "sand dunes desert"),
    "well": (["بئر", "جب", "سجن", "ظلام"], "dark stone well"),
    "night": (["ليل", "نجوم", "قمر", "رؤيا", "حلم"], "starry night sky"),
    "sea": (["بحر", "ماء", "نهر", "يم", "موج"], "ocean waves horizon"),
    "rain": (["مطر", "غيوم", "سحاب", "رعد"], "rain clouds sky"),
    "mountain": (["جبل", "طور", "صخر", "حجر"], "mountain peak rocky"),
    "paradise": (["جنة", "بستان", "روض", "أنهار", "نعيم"], "garden paradise river"),
    "fire": (["نار", "جهنم", "سعير", "حرق"], "fire flames dark"),
    "palm": (["شجر", "نخل", "زرع", "ثمار"], "palm trees oasis"),
    "wheat": (["قمح", "سنابل", "خير", "بركة"], "wheat field golden"),
    "spring": (["عين", "نبع"], "water spring nature"),
    "mosque": (["مسجد", "أذان", "منارة", "صلاة"], "mosque minaret sky"),
    "book": (["كتاب", "مصحف", "آيات", "ذكر"], "ancient quran book"),
    "caravan": (["قافلة", "تجار", "سفر", "طريق"], "desert caravan path"),
    "ruins": (["يوسف", "يعقوب", "إخوة", "بنيامين"], "ancient stone ruins"),
    "palace": (["ملك", "عرش", "خزائن", "سلطان"], "ancient palace throne"),
}

FEAR_KEYWORDS = ["خاف", "خوف", "رعب", "فزع", "تهديد", "خشية", "ظلمة", "وحدة"]
BATTLE_KEYWORDS = ["قتل", "حرب", "معركة", "سيف", "طعن", "دم", "صراع", "جيش"]
CONSPIRACY_KEYWORDS = ["حسد", "مكر", "كيد", "تآمر", "دسيسة", "خدعة", "غدر"]

DEFAULT_VISUAL = "islamic architecture mosque"
DEFAULT_SFX = ""
DEFAULT_VOICE = "(in a calm, dignified tone)"


def _safe_fallback(text: str) -> dict:
    """احتياطي: الكلمات المفتاحية (آمنة 100%)."""
    visual = DEFAULT_VISUAL
    for name, (keywords, query) in SCENE_MAP.items():
        if any(kw in text for kw in keywords):
            visual = query
            break

    sfx = ""
    if any(kw in text for kw in FEAR_KEYWORDS):
        sfx = "heartbeat slow"
    elif any(kw in text for kw in BATTLE_KEYWORDS):
        sfx = "sword clash"
    elif any(kw in text for kw in CONSPIRACY_KEYWORDS):
        sfx = "whisper quiet"

    # نبرة افتراضية
    voice_style = DEFAULT_VOICE
    if any(kw in text for kw in FEAR_KEYWORDS):
        voice_style = "(in a trembling, anxious tone)"
    elif any(kw in text for kw in BATTLE_KEYWORDS):
        voice_style = "(in a strong, urgent tone)"
    elif any(kw in text for kw in CONSPIRACY_KEYWORDS):
        voice_style = "(in a secretive, whispering tone)"

    return {"visual": visual, "sfx": sfx, "voice_style": voice_style}


def _is_safe(visual: str, sfx: str, voice_style: str) -> bool:
    """التحقق من أن الرد آمن شرعياً."""
    v_lower = visual.lower()
    s_lower = sfx.lower()

    if any(bad in v_lower for bad in FORBIDDEN_VISUAL):
        return False
    if any(bad in s_lower for bad in FORBIDDEN_SFX):
        return False
    if not voice_style.startswith("("):
        return False

    return True


def direct_scene(text: str) -> dict:
    """يرجع dict: {'visual': ..., 'sfx': ..., 'voice_style': ...}"""
    if not text.strip():
        return _safe_fallback("")

    try:
        pool = get_pool()
    except Exception:
        return _safe_fallback(text)

    safe_text = text[:500].replace("@@@", "")
    full_prompt = DIRECTOR_PROMPT.replace("@@@{text}@@@", f"@@@{safe_text}@@@")

    models = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-3.5-flash"]

    for model in models:
        try:
            key = pool.get_current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 120},
            }
            r = requests.post(url, json=body, timeout=25)

            if r.status_code != 200:
                continue

            cands = r.json().get("candidates") or []
            if not cands: continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            if not parts: continue
            raw = (parts[0].get("text") or "").strip()

            m = re.search(r"@@@\s*(\{.*?\})\s*@@@", raw, re.DOTALL)
            if not m:
                m = re.search(r"(\{.*?\})", raw, re.DOTALL)
            if not m: continue

            parsed = json.loads(m.group(1))
            visual = str(parsed.get("visual", "")).strip()[:60]
            sfx = str(parsed.get("sfx", "")).strip()[:40]
            voice_style = str(parsed.get("voice_style", "")).strip()[:80]

            if not visual:
                continue

            # فلترة أمنية
            if _is_safe(visual, sfx, voice_style):
                print(f"      🎬 Director (AI): visual='{visual}', sfx='{sfx or 'بدون'}', voice='{voice_style}'")
                return {"visual": visual, "sfx": sfx, "voice_style": voice_style}
            else:
                print(f"      ⚠️ Director: AI response rejected (unsafe), using fallback")
                continue

        except Exception as e:
            print(f"      ⚠️ Director: {model} exception: {str(e)[:80]}")
            continue

    # احتياطي
    result = _safe_fallback(text)
    print(f"      🎬 Director (fallback): visual='{result['visual']}', sfx='{result['sfx'] or 'بدون'}'")
    return result


if __name__ == "__main__":
    print(direct_scene("وابيضت عيناه من الحزن فهو كظيم"))
    print(direct_scene("قالوا تالله تفتأ تذكر يوسف"))
