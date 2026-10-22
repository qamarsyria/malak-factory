"""المخرج السينمائي: يحلل السرد ويختار الصور والأصوات (مع Cache)."""
import json
import re
import requests
from gemini_tts_pool import get_pool
from scene_cache import get_cached, set_cache

PROMPT = """You are an expert cinematic director for an Islamic documentary.

I will give you ONE sentence from the Arabic narration. Return a JSON object with:
- "visual": 2-4 English words for a Pixabay image
- "sfx": 1-3 English words for a Freesound sound effect

STRICT RULES:
1. NO humans, NO faces, NO women, NO men, NO children, NO hands, NO eyes. This is ISLAMIC content.
2. Only: mosques, minarets, desert, sand dunes, sky, stars, clouds, water, trees, mountains, stone architecture, ancient ruins, books, manuscripts, lanterns, prayer beads.
3. Visual must match the sentence's mood (grief -> rainy window; joy -> sunrise; danger -> storm; night -> stars).
4. SFX must be from nature only: wind, rain, birds, water, fire, crickets, thunder. NO music.
5. If in doubt, use: "islamic architecture mosque" + "wind".

Return ONLY JSON: @@@{"visual": "...", "sfx": "..."}@@@

Sentence: @@@{text}@@@
"""

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

    # 1. تحقق من الكاش
    cached = get_cached(text)
    if cached:
        print(f"      🎬 director (cache): visual='{cached['visual']}', sfx='{cached['sfx']}'")
        return cached

    # 2. الكلمات المفتاحية (بدون استهلاك جمني)
    # نستخدم الكلمات المفتاحية أولاً لأنها مضمونة ولا تستهلك حصة
    result = keyword_fallback(text)
    set_cache(text, result["visual"], result["sfx"])
    return result


if __name__ == "__main__":
    print(direct_scene("في مكة، بعد عام الحزن."))
