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
TTS_VOICE_FEMALE = "ar-EG-SalmaNeural"
TTS_RATE = "-10%"                  # edge-tts يبطئ قليلاً فقط، atempo يفعل الباقي
TTS_VOLUME = "+0%"
TTS_DELAY_BETWEEN_SEGMENTS = 2

# ===== القرآن (everyayah) =====
QURAN_RECITER = "Alafasy_128kbps"
QURAN_BASE = "https://everyayah.com/data"

# ===== خريطة الأصوات لكل نوع =====
VOICE_MAP = {
    "hadith":  "ar-EG-ShakirNeural",
    "faq":     "ar-EG-ShakirNeural",
    "riyad":   "ar-EG-ShakirNeural",
    "ruqyah":  "ar-SA-HamedNeural",
    "story":   "ar-EG-ShakirNeural",
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
SOURCE_ROTATION = [
    "riyad", "ruqyah", "faq", "hadith",
    "ruqyah", "faq", "hadith",
]
