"""كل الإعدادات في مكان واحد."""
import os

# ============================================================
# الفيديو
# ============================================================
VIDEO_W = 1080
VIDEO_H = 1920
VIDEO_FPS = 30
VIDEO_DURATION_MAX = 1200        # 20 دقيقة للحلقات القصصية
FONT_PATH = ""                   # سيُكتشف تلقائياً
FONT_SIZE = 70
TEXT_COLOR = "white"
TEXT_STROKE = "black"
TEXT_STROKE_W = 3
ACCENT_COLOR = "#FFD700"

# ============================================================
# الصوت - النظام الهجين (Gemini → Azure → Piper)
# ============================================================
# ترتيب المحركات
VOICE_ENGINE_PRIORITY = ["gemini", "azure", "piper"]

# Gemini TTS (الأساسي)
GEMINI_VOICE = "Charon"          # رجل عميق
GEMINI_VOICE_FEMALE = "Kore"     # نسائي (احتياطي)

# Azure TTS (الاحتياطي الأول)
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")
AZURE_VOICE = "ar-EG-ShakirNeural"   # رجل مصري
# خيارات Azure العربية:
#   ar-EG-ShakirNeural  → مصري - رجل
#   ar-EG-SalmaNeural   → مصري - امرأة
#   ar-SA-HamedNeural   → سعودي - رجل
#   ar-SA-ZariyahNeural → سعودي - امرأة
#   ar-SY-LaithNeural   → سوري - رجل
#   ar-SY-AmanyNeural   → سوري - امرأة
#   ar-JO-TaimNeural    → أردني - رجل
#   ar-JO-SanaNeural    → أردني - امرأة
#   ar-AE-HamdanNeural  → إماراتي - رجل
#   ar-AE-FatimaNeural  → إماراتي - امرأة
AZURE_RATE = "-10%"              # تباطؤ 10% من المصدر

# Piper TTS (الاحتياطي الأخير)
PIPER_MODEL = "/tmp/piper/ar_JO-kareem-medium.onnx"
PIPER_CONFIG = "/tmp/piper/ar_JO-kareem-medium.onnx.json"
PIPER_LENGTH_SCALE = 1.0         # 1.0 طبيعي | 1.2 بطيء 20%

# إعدادات عامة للصوت
TTS_VOICE = GEMINI_VOICE         # للتوافق مع الكود القديم
TTS_VOICE_FEMALE = GEMINI_VOICE_FEMALE
TTS_RATE = "-10%"
TTS_VOLUME = "+0%"
TTS_DELAY_BETWEEN_SEGMENTS = 3   # ثواني بين طلبات TTS

# صيغة الصوت الموحّدة (للدمج)
TARGET_SAMPLE_RATE = "44100"
TARGET_CHANNELS = "1"
TARGET_BITRATE = "128k"

# ============================================================
# القرآن (everyayah)
# ============================================================
QURAN_RECITER = "Alafasy_128kbps"    # مشاري العفاسي
# خيارات أخرى:
#   "Husary_128kbps"                 → الحصري
#   "Minshawy_Murattal_128kbps"      → المنشاوي
#   "Abdul_Basit_Murattal_192kbps"   → عبد الباسط
#   "Abdurrahmaan_As-Sudais_192kbps" → السديس
#   "Hudhaify_128kbps"               → الحذيفي
#   "Nasser_Alqatami_128kbps"        → القطامي
#   "Yasser_Ad-Dussary_128kbps"      → الدوسري
QURAN_BASE = "https://everyayah.com/data"

# ============================================================
# خريطة الأصوات لكل نوع محتوى
# ============================================================
VOICE_MAP = {
    "hadith":  {"gemini": "Charon",       "azure": "ar-EG-ShakirNeural"},
    "faq":     {"gemini": "Charon",       "azure": "ar-EG-ShakirNeural"},
    "riyad":   {"gemini": "Charon",       "azure": "ar-EG-ShakirNeural"},
    "ruqyah":  {"gemini": "Charon",       "azure": "ar-SA-HamedNeural"},
    "story":   {"gemini": "Charon",       "azure": "ar-EG-ShakirNeural"},
}

# ============================================================
# الخلفيات (Pixabay)
# ============================================================
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_QUERY = "mosque,islamic,nature,sky,clouds,desert,stars"
BG_FALLBACK = "assets/bg/default.jpg"
BG_OVERLAY_OPACITY = 0.45
KEN_BURNS_ZOOM = 1.05

# مجلدات الخلفيات
BACKGROUNDS_DIR = "assets/backgrounds"
BG_IMAGES_DIR = "assets/backgrounds/images"
BG_VIDEOS_DIR = "assets/backgrounds/videos"

# ============================================================
# المسارات
# ============================================================
# مصادر المحتوى
FAQ_JSON = "data/faq-data.json"
HADITH_JSON = "data/hadith-db.json"
RIYAD_JSON = "data/riyad-salihin.json"
RUQYAH_JSON = "data/ruqyah/ruqyah.json"

# القصص
STORIES_DIR = "data/stories"

# الحالة
STATE_FILE = "state/published.json"

# الخطوط
FONT_DIR = "assets/fonts"

# الصوتيات
AUDIO_DIR = "assets/audio"
MUSIC_DIR = "assets/audio/music"
SFX_DIR = "assets/audio/sfx"

# ============================================================
# Gemini API
# ============================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_MODELS_TO_TRY = [
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]

# ============================================================
# التحقق
# ============================================================
DORAR_API = "https://dorar.net/dorar_api.json"
QURAN_API = "https://api.alquran.cloud/v1"

# ============================================================
# الهاشتاجات
# ============================================================
HASHTAGS = "#إسلاميات #قرآن #حديث #قصص_الأنبياء #رقية_شرعية #دين #تذكير #قمر_الشام #ملاك_المعرفة"

# ============================================================
# تيليجرام
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# فيسبوك
# ============================================================
FACEBOOK_PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_TOKEN", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")

# ============================================================
# يوتيوب
# ============================================================
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")
YT_PRIVACY = "public"

# ============================================================
# الموقع
# ============================================================
SITE_URL = "https://qamarsyria.github.io/qamar-malak/"

# ============================================================
# توزيع المصادر اليومية (7 أيام)
# ============================================================
# السبت=0 الأحد=1 الاثنين=2 الثلاثاء=3 الأربعاء=4 الخميس=5 الجمعة=6
SOURCE_ROTATION = [
    "riyad",    # السبت
    "story",    # الأحد - قصة الأنبياء
    "faq",      # الاثنين
    "hadith",   # الثلاثاء
    "story",    # الأربعاء - قصة الأنبياء
    "faq",      # الخميس
    "hadith",   # الجمعة
]

# ============================================================
# إعدادات القصص
# ============================================================
# القصص المتاحة
STORIES_AVAILABLE = [
    "yusuf",     # يوسف
    "adam",      # آدم
    "nuh",       # نوح
    "ibrahim",   # إبراهيم
    "musa",      # موسى
]

# تقسيم سورة يوسف (111 آية) لحلقات درامية
YUSUF_EPISODES = [
    (1, 3),      # مقدمة السورة
    (4, 6),      # الرؤيا وبشارة يعقوب
    (7, 10),     # حسد الإخوة والمؤامرة
    (11, 14),    # طلب الإخوة وقلق يعقوب
    (15, 18),    # إلقاء يوسف في الجب
    (19, 22),    # التقاطه وبيعه
    (23, 29),    # مراودة امرأة العزيز
    (30, 35),    # نسوة المدينة والسجن
    (36, 42),    # دعوته في السجن
    (43, 49),    # رؤيا الملك وتفسيرها
    (50, 53),    # براءة يوسف
    (54, 57),    # تمكينه على خزائن مصر
    (58, 62),    # أول لقاء مع إخوته
    (63, 68),    # العودة إلى يعقوب
    (69, 76),    # بنيامين والسقاية
    (77, 82),    # اتهام بنيامين
    (83, 87),    # حزن يعقوب
    (88, 92),    # عودة الإخوة والتعارف
    (93, 98),    # القميص والبشارة
    (99, 101),   # اللقاء الكبير والدعاء
    (102, 111),  # الخاتمة والعبر
]

# ============================================================
# إعدادات الفيديو (المزيد)
# ============================================================
# معايير الحلقة
EPISODE_MIN_DURATION = 600       # 10 دقائق
EPISODE_MAX_DURATION = 1200      # 20 دقيقة
NARRATION_MIN_SENTENCES = 8      # 8 جمل لكل مقطع سرد
NARRATION_MAX_SENTENCES = 12

# ============================================================
# إعدادات شرعية (لا تُغيَّر)
# ============================================================
FORBIDDEN_VISUALS = [
    "prophets",          # الأنبياء - لا تصوير
    "companions",        # الصحابة
    "mothers_of_believers",  # أمهات المؤمنين
    "ahl_al_bayt",       # آل البيت
    "angels",            # الملائكة
    "khalifa_rashidun",  # الخلفاء الراشدون
]

# القرآن: لا موسيقى، لا مؤثرات، لا قطع
QURAN_RULES = {
    "no_music": True,
    "no_sfx": True,
    "no_cut": True,           # لا قطع آية أبداً
    "complete_ayah_only": True,
}
