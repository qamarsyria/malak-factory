"""المخرج السينمائي: يحدد الصورة (لكل مشهد) والمؤثر (حسب الكلمات المفتاحية)."""
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

# --- الكلمات المفتاحية للمشاهد (الصور) ---
SCENE_KEYWORDS = [
    (["مكة", "الكعبة", "الحرم", "بيت الله"], "kaaba mecca ancient stone"),
    (["مصر", "فرعون", "القصر", "العزيز", "الملك"], "ancient egyptian palace"),
    (["صحراء", "رمل", "قفر", "بيداء", "كنعان", "تيه"], "sand dunes desert"),
    (["بئر", "جب", "سجن", "ظلام", "ظلمة"], "dark stone well"),
    (["ليل", "نجوم", "قمر", "منام", "رؤيا", "حلم"], "starry night sky desert"),
    (["بحر", "ماء", "نهر", "يم", "موج", "سفينة"], "ocean waves horizon"),
    (["مطر", "غيوم", "سحاب", "رعد", "برق"], "rain clouds sky"),
    (["جبل", "طور", "صخر", "حجر", "كهف", "غار"], "mountain peak rocky"),
    (["جنة", "بستان", "روض", "أنهار", "نعيم"], "garden paradise river"),
    (["نار", "جهنم", "سعير", "حرق", "عذاب"], "fire flames"),
    (["شجر", "نخل", "زرع", "ثمار", "حديقة"], "palm trees oasis"),
    (["قمح", "سنابل", "زرع", "خير", "بركة"], "wheat field golden"),
    (["عين", "نبع", "ماء"], "water spring nature"),
    (["مسجد", "أذان", "منارة", "صلاة"], "mosque minaret sky"),
    (["كتاب", "مصحف", "آيات", "ذكر"], "ancient quran book"),
    (["قافلة", "تجار", "سفر", "طريق"], "desert caravan path"),
    (["يوسف", "يعقوب", "إخوة", "بنيامين"], "ancient stone ruins"),
    (["ملك", "عرش", "خزائن", "سلطان"], "ancient palace throne"),
    (["غذاء", "طعام", "مائدة", "سقاية"], "ancient stone bowls"),
    (["امرأة", "نساء", "نسوة", "سيدة"], "ancient palace interior"),
]

# --- الكلمات المفتاحية للمؤثرات (فقط الخوف، المعركة، المكيدة) ---
SFX_KEYWORDS = [
    # الخوف
    (["خاف", "خوف", "رعب", "فزع", "رهبة", "تهديد", "ظلام", "وحدة", "فزع"], "fear"),
    # المعركة
    (["قتل", "حرب", "معركة", "سيف", "طعن", "دم", "صراع", "قتال", "جيش", "سلاح"], "battle"),
    # المكيدة
    (["حسد", "مكر", "كيد", "تآمر", "دسيسة", "خدعة", "غدر", "وسوسة"], "conspiracy"),
]

# المؤثرات المتاحة لكل حالة (سيتم اختيارها عشوائياً)
SFX_LIBRARY = {
    "fear": ["heartbeat", "rain", "wind_dark"],
    "battle": ["sword", "horse_gallop", "battle_cry"],
    "conspiracy": ["whisper", "wind_light", "owl"],
}

# المشهد الافتراضي (إذا لم يطابق أي مشهد)
DEFAULT_VISUAL = "islamic architecture mosque"

def direct_scene(text: str) -> dict:
    if not text.strip():
        return {"visual": DEFAULT_VISUAL, "sfx": ""}

    cached = _get_cached(text)
    if cached:
        return cached

    # 1. تحديد الصورة (بناءً على المشهد)
    visual = DEFAULT_VISUAL
    for keywords, scene_visual in SCENE_KEYWORDS:
        if any(kw in text for kw in keywords):
            visual = scene_visual
            break

    # 2. تحديد المؤثر (فقط إذا طابقت كلمة مفتاحية)
    sfx = ""
    for keywords, sfx_type in SFX_KEYWORDS:
        if any(kw in text for kw in keywords):
            # اختر مؤثراً عشوائياً من المكتبة
            import random
            sfx = random.choice(SFX_LIBRARY[sfx_type])
            break

    _set_cache(text, visual, sfx)
    return {"visual": visual, "sfx": sfx}

if __name__ == "__main__":
    print(direct_scene("في مكة، بعد عام الحزن."))
    print(direct_scene("وخاف يعقوب على ابنه من إخوته."))
    print(direct_scene("وخطط الإخوة للتخلص من يوسف."))
