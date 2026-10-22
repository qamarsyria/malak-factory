"""مخرج سينمائي ذكي (بدون AI): يختار الصور والمؤثرات بناءً على قواميس دقيقة."""
import re

# ============================================================
# قاموس المشاهد (للصور) — كل مشهد له كلمات مفتاحية وصورة محددة
# ============================================================
SCENE_DICT = {
    "mecca": {
        "keywords": ["مكة", "الكعبة", "الحرم", "بيت الله", "قريش", "أم القرى"],
        "query": "kaaba mecca ancient stone"
    },
    "egypt": {
        "keywords": ["مصر", "فرعون", "القصر", "العزيز", "الملك", "العرش", "خزائن", "سنابل"],
        "query": "ancient egyptian palace nile"
    },
    "desert": {
        "keywords": ["صحراء", "رمل", "قفر", "بيداء", "كنعان", "تيه", "أرض", "سهل"],
        "query": "sand dunes desert"
    },
    "well": {
        "keywords": ["بئر", "جب", "سجن", "ظلام", "ظلمة", "قاع", "حفرة"],
        "query": "dark stone well"
    },
    "night": {
        "keywords": ["ليل", "نجوم", "قمر", "منام", "رؤيا", "حلم", "كواكب", "سهاد", "ظلام"],
        "query": "starry night sky desert"
    },
    "sea": {
        "keywords": ["بحر", "ماء", "نهر", "يم", "موج", "سفينة", "غرق"],
        "query": "ocean waves horizon"
    },
    "rain": {
        "keywords": ["مطر", "غيوم", "سحاب", "رعد", "برق", "عاصفة"],
        "query": "rain clouds sky"
    },
    "mountain": {
        "keywords": ["جبل", "طور", "صخر", "حجر", "كهف", "غار"],
        "query": "mountain peak rocky"
    },
    "paradise": {
        "keywords": ["جنة", "بستان", "روض", "أنهار", "نعيم", "حدائق"],
        "query": "garden paradise river"
    },
    "fire": {
        "keywords": ["نار", "جهنم", "سعير", "حرق", "عذاب", "لهب"],
        "query": "fire flames dark"
    },
    "wheat": {
        "keywords": ["قمح", "سنابل", "زرع", "خير", "بركة", "حصاد"],
        "query": "wheat field golden"
    },
    "water": {
        "keywords": ["عين", "نبع", "ماء", "نهر", "سقاية"],
        "query": "water spring nature"
    },
    "mosque": {
        "keywords": ["مسجد", "أذان", "منارة", "صلاة", "محراب"],
        "query": "mosque minaret sky"
    },
    "book": {
        "keywords": ["كتاب", "مصحف", "آيات", "ذكر", "قرآن"],
        "query": "ancient quran book"
    },
    "caravan": {
        "keywords": ["قافلة", "تجار", "سفر", "طريق", "بضاعة"],
        "query": "desert caravan path"
    },
    "ruins": {
        "keywords": ["يوسف", "يعقوب", "إخوة", "بنيامين", "أب", "ابن"],
        "query": "ancient stone ruins"
    },
    "palace": {
        "keywords": ["ملك", "عرش", "خزائن", "سلطان", "قصر", "عزيز"],
        "query": "ancient palace throne"
    },
    "food": {
        "keywords": ["غذاء", "طعام", "مائدة", "سقاية", "خبز"],
        "query": "ancient stone bowls"
    },
    "woman": {
        "keywords": ["امرأة", "نساء", "نسوة", "سيدة", "زوجة"],
        "query": "ancient palace interior"
    },
}

# ============================================================
# قاموس المؤثرات الصوتية (فقط الخوف، المعركة، المكيدة)
# ============================================================
SFX_DICT = {
    "fear": {
        "keywords": ["خاف", "خوف", "رعب", "فزع", "رهبة", "تهديد", "خشية", "ارتعب", "فزع", "أشفق", "ظلمة", "وحدة"],
        "query": "heartbeat slow"  # أو rain / wind
    },
    "battle": {
        "keywords": ["قتل", "حرب", "معركة", "سيف", "طعن", "دم", "صراع", "قتال", "جيش", "سلاح", "رمح", "قوس"],
        "query": "sword clash"
    },
    "conspiracy": {
        "keywords": ["حسد", "مكر", "كيد", "تآمر", "دسيسة", "خدعة", "غدر", "كاد", "مكيدة", "وسوسة", "دبر", "أسرّ"],
        "query": "whisper quiet"
    }
}

# الصورة الافتراضية (إذا لم يطابق أي مشهد)
DEFAULT_VISUAL = "islamic architecture mosque"

def _find_scene(text):
    """يبحث عن المشهد المناسب للنص."""
    for scene_name, data in SCENE_DICT.items():
        if any(kw in text for kw in data["keywords"]):
            return data["query"]
    return DEFAULT_VISUAL

def _find_sfx(text):
    """يبحث عن المؤثر المناسب للنص (فقط للخوف/المعركة/المكيدة)."""
    for sfx_type, data in SFX_DICT.items():
        if any(kw in text for kw in data["keywords"]):
            return data["query"]
    return ""  # لا مؤثر افتراضي

def direct_scene(text: str) -> dict:
    """يرجع dict: {'visual': ..., 'sfx': ...}"""
    if not text.strip():
        return {"visual": DEFAULT_VISUAL, "sfx": ""}
    
    visual = _find_scene(text)
    sfx = _find_sfx(text)
    
    return {"visual": visual, "sfx": sfx}
