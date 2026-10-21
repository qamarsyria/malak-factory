"""المخرج السينمائي: يحلل السرد ويختار الصور والأصوات المناسبة لكل جملة."""
import json
import re
import requests
from gemini_tts_pool import get_pool

PROMPT = """You are an expert cinematic director for an Islamic documentary about Prophet Yusuf (Joseph) and other Quranic stories.

I will give you ONE sentence from the Arabic narration. You must return a JSON object with:
- "visual": 2-4 English words describing a background image for Pixabay
- "sfx": 1-3 English words describing a natural sound for Freesound

Rules:
1. NO humans, NO faces, NO prophets, NO idols. Only: nature, architecture, objects, sky, landscapes.
2. Match the sentence's exact content and emotion.
3. Visual must be specific (e.g., "kaaba ancient stone" not "islamic"), because generic queries return wrong images.
4. SFX must be from nature only: wind, rain, birds, water, fire, crickets, thunder, etc.
5. NO MUSIC. NO instruments.
6. If the sentence mentions grief, use "rain window" or "autumn leaves". If joy, use "sunrise meadow" or "birds chirping". If danger, use "storm dark clouds". If night, use "starry sky night". If prophecy/dream, use "starry night desert". If Egypt/palace, use "ancient stone palace". If well/prison, use "dark stone walls".

Return ONLY valid JSON (no markdown, no explanation):
@@@{"visual": "...", "sfx": "..."}@@@

Sentence: @@@{text}@@@
"""


# خريطة كلمات مفتاحية (احتياطي ذكي إذا فشل الذكاء الاصطناعي)
KEYWORD_MAP = [
    (["مكة", "الكعبة", "بيت الله", "الحرم"], "kaaba mecca stone", "wind"),
    (["مصر", "فرعون", "القصر", "العزيز"], "ancient egyptian palace", "crowd distant"),
    (["صحراء", "رمل", "قفر", "بيداء", "كنعان"], "sand dunes desert", "wind"),
    (["بئر", "جب", "سجن", "ظلام"], "dark stone well", "water drip"),
    (["ليل", "نجوم", "قمر", "ظلمة"], "starry night sky", "crickets"),
    (["بحر", "ماء", "نهر", "يم"], "ocean waves horizon", "ocean waves"),
    (["مطر", "سحاب", "غيوم"], "rain clouds sky", "rain"),
    (["حزن", "بكاء", "دمع", "أسى", "حسرة"], "rain window", "sad bird"),
    (["فرح", "بشر", "سرور", "عيد", "بهجة"], "sunrise meadow", "birds chirping"),
    (["رؤيا", "حلم", "منام", "كواكب"], "starry night desert", "wind"),
    (["ملك", "عرش", "خزائن", "سلطان"], "ancient palace throne", "crowd distant"),
    (["قمح", "زرع", "سنابل", "خير", "بركة"], "wheat field golden", "birds chirping"),
    (["حسد", "مكر", "كيد", "تآمر", "دسيسة"], "dark forest fog", "whisper"),
    (["خوف", "رعب", "فزع", "رهبة"], "storm dark clouds", "thunder"),
    (["غضب", "عذاب", "هلاك", "دمار"], "volcanic eruption lava", "fire"),
    (["قتال", "حرب", "معركة", "سيوف"], "desert battlefield", "sword clash"),
    (["جبل", "طور", "صخر", "حجر"], "mountain peak rocky", "wind"),
    (["جنة", "بستان", "روض", "أنهار", "نعيم"], "garden paradise river", "birds chirping"),
    (["نار", "جهنم", "سعير", "حرق"], "fire flames", "fire"),
    (["ريح", "عاصفة", "هبوب", "زوبعة"], "storm clouds sky", "wind"),
    (["شجر", "نخل", "زرع", "ثمار", "حديقة"], "palm trees oasis", "wind"),
    (["كهف", "غار", "ظلام", "صخور"], "dark cave", "water drip"),
    (["عين", "نبع", "ماء", "بئر"], "water spring nature", "water spring"),
    (["صوت", "أذان", "تكبير", "منارة"], "minaret mosque sky", "wind"),
    (["كتاب", "مصحف", "آيات", "ذكر"], "ancient quran book", "wind"),
    (["قافلة", "تجار", "سفر", "طريق"], "desert caravan path", "wind"),
    (["بنيامين", "يوسف", "يعقوب", "إخوة"], "ancient stone ruins", "wind"),
    (["امرأة", "نساء", "نسوة", "سيدة"], "ancient palace interior", "wind"),
    (["غذاء", "طعام", "مائدة", "سقاية"], "ancient stone bowls", "water drip"),
]


def keyword_fallback(text: str) -> dict:
    """احتياطي: البحث بالكلمات المفتاحية العربية."""
    for keywords, visual, sfx in KEYWORD_MAP:
        if any(kw in text for kw in keywords):
            print(f"      🎬 director (keyword): visual='{visual}', sfx='{sfx}'")
            return {"visual": visual, "sfx": sfx}

    print(f"      🎬 director (default): visual='sand dunes desert', sfx='wind'")
    return {"visual": "sand dunes desert", "sfx": "wind"}


def direct_scene(text: str) -> dict:
    if not text.strip():
        return keyword_fallback("")

    try:
        pool = get_pool()
    except Exception as e:
        print(f"      ⚠️ director: no gemini pool ({e}), using keyword fallback")
        return keyword_fallback(text)

    # إصلاح: استبدال النص يدوياً بدل .format() لتجنب خطأ الأقواس
    safe_text = text[:400].replace("@@@", "").replace("\\", "")
    full_prompt = PROMPT.replace("@@@{text}@@@", f"@@@{safe_text}@@@")

    # ترتيب الموديلات: نفضل flash العادي على lite
    models = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-3.5-flash", "gemini-flash-lite-latest"]

    for model in models:
        try:
            key = pool.get_current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {
                    "temperature": 0.7,
                    "topP": 0.9,
                    "maxOutputTokens": 100,
                },
            }
            r = requests.post(url, json=body, timeout=25)

            if r.status_code == 429:
                print(f"      ⚠️ director: {model} rate limited")
                continue
            if r.status_code != 200:
                print(f"      ⚠️ director: {model} HTTP {r.status_code}")
                continue

            data = r.json()
            cands = data.get("candidates") or []
            if not cands: continue

            parts = (cands[0].get("content") or {}).get("parts") or []
            if not parts: continue

            raw = (parts[0].get("text") or "").strip()
            print(f"      🤖 director raw: {raw[:120]}")

            # استخراج JSON من أي صيغة
            m = re.search(r"@@@\s*(\{.*?\})\s*@@@", raw, re.DOTALL)
            if not m:
                m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if not m:
                m = re.search(r"(\{.*?\})", raw, re.DOTALL)

            if not m:
                print(f"      ⚠️ director: no JSON found")
                continue

            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError as e:
                print(f"      ⚠️ director: invalid JSON ({e})")
                continue

            visual = str(parsed.get("visual", "")).strip()
            sfx = str(parsed.get("sfx", "")).strip()

            if not visual or not sfx:
                print(f"      ⚠️ director: empty fields")
                continue

            # تنظيف
            visual = re.sub(r"[^a-zA-Z0-9\s]", "", visual)[:60].strip()
            sfx = re.sub(r"[^a-zA-Z0-9\s]", "", sfx)[:40].strip()

            print(f"      🎬 director: visual='{visual}', sfx='{sfx}'")
            return {"visual": visual, "sfx": sfx}

        except Exception as e:
            print(f"      ⚠️ director: {model} exception: {str(e)[:80]}")
            continue

    print(f"      ⚠️ director: all models failed, using keyword fallback")
    return keyword_fallback(text)
