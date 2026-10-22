"""المخرج السينمائي: يحلل السرد ويختار الصور والأصوات المناسبة لكل جملة."""
import json
import re
import requests
from gemini_tts_pool import get_pool

PROMPT = """You are an expert cinematic director for an Islamic documentary about Quranic stories.

I will give you ONE sentence from the Arabic narration. Return a JSON object with:
- "visual": 2-4 English words for a Pixabay image
- "sfx": 1-3 English words for a Freesound sound effect

STRICT RULES:
1. NO humans, NO faces, NO women, NO men, NO children, NO hands, NO eyes. This is ISLAMIC content.
2. Only: mosques, minarets, desert, sand dunes, sky, stars, clouds, water, trees, mountains, stone architecture, ancient ruins, books, manuscripts, lanterns, prayer beads.
3. Visual must match the sentence's mood (grief → rainy window; joy → sunrise; danger → storm; night → stars).
4. SFX must be from nature only: wind, rain, birds, water, fire, crickets, thunder. NO music.
5. If in doubt, use: "islamic architecture mosque" + "wind".

Return ONLY JSON: @@@{"visual": "...", "sfx": "..."}@@@

Sentence: @@@{text}@@@
"""

# كلمات مفتاحية إسلامية (احتياطي ذكي)
KEYWORD_MAP = [
    (["مكة", "الكعبة", "الحرم"], "kaaba mecca", "wind"),
    (["مصر", "فرعون", "القصر"], "ancient egypt palace", "wind"),
    (["صحراء", "رمل", "كنعان", "بيداء"], "sand dunes desert", "wind"),
    (["بئر", "جب", "سجن"], "ancient stone well", "water drip"),
    (["ليل", "نجوم", "قمر"], "starry night sky", "crickets"),
    (["بحر", "ماء", "نهر"], "ocean waves", "ocean waves"),
    (["مطر", "غيوم", "سحاب"], "rain clouds", "rain"),
    (["حزن", "دمع", "أسى", "حسرة"], "rain window autumn", "sad bird"),
    (["فرح", "بشر", "سرور", "بهجة"], "sunrise meadow", "birds chirping"),
    (["رؤيا", "حلم", "كواكب"], "starry desert night", "wind"),
    (["ملك", "عرش", "خزائن"], "ancient palace", "wind"),
    (["قمح", "زرع", "سنابل"], "wheat field golden", "birds chirping"),
    (["حسد", "مكر", "كيد", "تآمر"], "dark forest fog", "whisper"),
    (["خوف", "رعب", "فزع"], "storm dark clouds", "thunder"),
    (["غضب", "عذاب", "هلاك"], "volcanic lava", "fire"),
    (["جبل", "طور", "صخر"], "mountain peak", "wind"),
    (["جنة", "بستان", "نعيم"], "garden paradise", "birds chirping"),
    (["نار", "جهنم", "سعير"], "fire flames", "fire"),
    (["ريح", "عاصفة"], "storm clouds", "wind"),
    (["شجر", "نخل", "ثمار"], "palm trees oasis", "wind"),
    (["كهف", "غار"], "dark cave", "water drip"),
    (["عين", "نبع"], "water spring", "water spring"),
    (["أذان", "منارة", "مسجد"], "mosque minaret", "wind"),
    (["كتاب", "مصحف", "آيات"], "ancient quran book", "wind"),
    (["قافلة", "تجار"], "desert caravan", "wind"),
    (["يوسف", "يعقوب", "إخوة"], "ancient stone ruins", "wind"),
    (["غذاء", "طعام", "مائدة"], "ancient stone bowls", "water drip"),
]

# الصورة الافتراضية الآمنة (إسلامية 100%)
DEFAULT_VISUAL = "islamic architecture mosque"
DEFAULT_SFX = "wind"


def keyword_fallback(text: str) -> dict:
    for keywords, visual, sfx in KEYWORD_MAP:
        if any(kw in text for kw in keywords):
            print(f"      🎬 director (keyword): visual='{visual}', sfx='{sfx}'")
            return {"visual": visual, "sfx": sfx}
    print(f"      🎬 director (default): {DEFAULT_VISUAL}")
    return {"visual": DEFAULT_VISUAL, "sfx": DEFAULT_SFX}


def direct_scene(text: str) -> dict:
    if not text.strip():
        return keyword_fallback("")

    try:
        pool = get_pool()
    except Exception:
        return keyword_fallback(text)

    safe_text = text[:400].replace("@@@", "")
    full_prompt = PROMPT.replace("@@@{text}@@@", f"@@@{safe_text}@@@")

    models = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-3.5-flash"]

    for model in models:
        try:
            key = pool.get_current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 80},
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
            visual = re.sub(r"[^a-zA-Z0-9\s]", "", str(parsed.get("visual", "")))[:60].strip()
            sfx = re.sub(r"[^a-zA-Z0-9\s]", "", str(parsed.get("sfx", "")))[:40].strip()

            # فلترة أمنية: منع كلمات ممنوعة
            forbidden = ["woman", "women", "girl", "man", "people", "human", "person", "child", "face", "hand", "body"]
            if any(bad in visual.lower() for bad in forbidden):
                print(f"      ⚠️ director: forbidden word in '{visual}', replacing")
                visual = DEFAULT_VISUAL

            # إضافة "islamic" لضمان النتيجة
            if "islamic" not in visual.lower() and "mosque" not in visual.lower():
                visual = f"islamic {visual}"

            print(f"      🎬 director: visual='{visual}', sfx='{sfx}'")
            return {"visual": visual, "sfx": sfx if sfx else DEFAULT_SFX}

        except Exception:
            continue

    return keyword_fallback(text)
