"""كل الإعدادات في مكان واحد."""
import os

# ===== الفيديو =====
VIDEO_W = 1080
VIDEO_H = 1920
VIDEO_FPS = 30
VIDEO_DURATION_MAX = 1200
FONT_PATH = ""
FONT_SIZE = 70
TEXT_COLOR = "white"
TEXT_STROKE = "black"
TEXT_STROKE_W = 3
ACCENT_COLOR = "#FFD700"

# ===== الصوت =====
TTS_VOICE = "ar-EG-ShakirNeural"   # رجل مصري فصيح
# خيارات أخرى:
#   "ar-SA-HamedNeural"   → سعودي
#   "ar-SY-LaithNeural"   → سوري
#   "ar-JO-TaimNeural"    → أردني
#   "ar-AE-HamdanNeural"  → إماراتي

TTS_VOICE_FEMALE = "ar-EG-SalmaNeural"   # للقصص النسائية (احتياطي)
# خيارات نسائية:
#   "ar-SA-ZariyahNeural" → سعودي
#   "ar-SY-AmanyNeural"   → سوري
#   "ar-JO-SanaNeural"    → أردني
#   "ar-AE-FatimaNeural"  → إماراتي

TTS_RATE = "-25%"        # تباطؤ 25% للسرد القصصي
TTS_VOLUME = "+0%"
TTS_DELAY_BETWEEN_SEGMENTS = 2   # ثواني بين الطلبات

# ===== القرآن (everyayah) =====
QURAN_RECITER = "Alafasy_128kbps"   # مشاري العفاسي
# خيارات أخرى:
#   "Husary_128kbps"                → الحصري
#   "Minshawy_Murattal_128kbps"     → المنشاوي
#   "Abdul_Basit_Murattal_192kbps"  → عبد الباسط
#   "Abdurrahmaan_As-Sudais_192kbps"→ السديس
#   "Hudhaify_128kbps"              → الحذيفي
#   "Nasser_Alqatami_128kbps"       → القطامي
#   "Yasser_Ad-Dussary_128kbps"     → الدوسري
QURAN_BASE = "https://everyayah.com/data"

# ===== خريطة الأصوات لكل نوع =====
VOICE_MAP = {
    "hadith":  "ar-EG-ShakirNeural",   # أحاديث: رجل مصري
    "faq":     "ar-EG-ShakirNeural",   # مسائل: رجل مصري
    "riyad":   "ar-EG-ShakirNeural",   # رياض الصالحين: رجل مصري
    "ruqyah":  "ar-SA-HamedNeural",    # رقية: رجل سعودي وقور
    "story":   "ar-EG-ShakirNeural",   # قصص: رجل مصري فصيح
}

# ===== الخلفية =====
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_QUERY = "mosque,islamic,nature,sky,clouds,desert,stars"
BG_FALLBACK = "assets/bg/default.jpg"
BG_OVERLAY_OPACITY = 0.45
KEN_BURNS_ZOOM = 1.05

# ===== المسارات =====
FAQ_JSON = "data/faq-data.json"
HADITH_JSON = "data/hadith-db.json"
RIYAD_JSON = "data/riyad-salihin.json"
RUQYAH_JSON = "data/ruqyah/ruqyah.json"
STATE_FILE = "state/published.json"

# ===== Gemini =====
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-flash-latest"

# ===== التحقق =====
DORAR_API = "https://dorar.net/dorar_api.json"
QURAN_API = "https://api.alquran.cloud/v1"

# ===== الهاشتاجات =====
HASHTAGS = "#إسلاميات #قرآن #حديث #رقية_شرعية #دين #تذكير #قمر_الشام #ملاك_المعرفة"

# ===== تيليجرام =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ===== فيسبوك =====
FACEBOOK_PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_TOKEN", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")

# ===== يوتيوب =====
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")
YT_PRIVACY = "public"

# ===== الموقع =====
SITE_URL = "https://qamarsyria.github.io/qamar-malak/"

# ===== توزيع المصادر (7 أيام) =====
# السبت=0 الأحد=1 الاثنين=2 الثلاثاء=3 الأربعاء=4 الخميس=5 الجمعة=6
SOURCE_ROTATION = [
    "riyad",    # السبت
    "ruqyah",   # الأحد
    "faq",      # الاثنين
    "hadith",   # الثلاثاء
    "ruqyah",   # الأربعاء
    "faq",      # الخميس
    "hadith",   # الجمعة
]
