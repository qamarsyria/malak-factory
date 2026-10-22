"""المخرج السينمائي: يحلل السرد ويختار الصور والأصوات (بدون استهلاك جمني)."""
import hashlib
import json
from pathlib import Path

CACHE_FILE = "out/director_cache.json"
_cache = {}
_loaded = False

def _load():
    global _cache, _loaded
    if _loaded: return
    try:
        if Path(CACHE_FILE).exists():
            _cache = json.loads(Path(CACHE_FILE).read_text(encoding="utf-8"))
    except Exception:
        _cache = {}
    _loaded = True

def _save():
    try:
        Path(CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(CACHE_FILE).write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _get_cached(text: str):
    _load()
    key = hashlib.md5(text.strip()[:200].encode("utf-8")).hexdigest()
    return _cache.get(key)

def _set_cache(text: str, visual: str, sfx: str):
    _load()
    key = hashlib.md5(text.strip()[:200].encode("utf-8")).hexdigest()
    _cache[key] = {"visual": visual, "sfx": sfx}
    _save()

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

def direct_scene(text: str) -> dict:
    if not text.strip():
        return {"visual": DEFAULT_VISUAL, "sfx": DEFAULT_SFX}

    cached = _get_cached(text)
    if cached:
        return cached

    for keywords, visual, sfx in KEYWORD_MAP:
        if any(kw in text for kw in keywords):
            result = {"visual": visual, "sfx": sfx}
            _set_cache(text, visual, sfx)
            return result

    result = {"visual": DEFAULT_VISUAL, "sfx": DEFAULT_SFX}
    _set_cache(text, result["visual"], result["sfx"])
    return result

if __name__ == "__main__":
    print(direct_scene("في مكة، بعد عام الحزن."))
