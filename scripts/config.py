"""كل الإعدادات في مكان واحد. لا تلمس باقي الملفات."""
import os

# ===== الفيديو =====
VIDEO_W = 1080
VIDEO_H = 1920
VIDEO_FPS = 30
VIDEO_DURATION_MAX = 60
FONT_PATH = ""  # سيُكتشف تلقائياً
FONT_SIZE = 70
TEXT_COLOR = "white"
TEXT_STROKE = "black"
TEXT_STROKE_W = 3
ACCENT_COLOR = "#FFD700"

# ===== الصوت =====
TTS_VOICE = "ar-EG-ShakirNeural"
TTS_RATE = "+0%"
TTS_VOLUME = "+0%"

# ===== الخلفية =====
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_QUERY = "mosque,islamic,nature,sky,clouds"
BG_FALLBACK = "assets/bg/default.jpg"
BG_OVERLAY_OPACITY = 0.45
KEN_BURNS_ZOOM = 1.05

# ===== المسارات =====
FAQ_FILE = "data/faq-data.js"
HADITH_FILE = "data/hadith-db.js"
RIYAD_FILE = "data/riyad-salihin.js"
RIYAD_JSON = "data/riyad-salihin.json"
FAQ_JSON = "data/faq-data.json"
HADITH_JSON = "data/hadith-db.json"
STATE_FILE = "state/published.json"

# ===== Gemini =====
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# ===== التحقق =====
DORAR_API = "https://dorar.net/dorar_api.json"
QURAN_API = "https://api.alquran.cloud/v1"
HADITH_CDN = "https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1"

# ===== الهاشتاجات =====
HASHTAGS = "#إسلاميات #قرآن #حديث #دين #تذكير #قمر_الشام #ملاك_المعرفة"

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
YT_PRIVACY = "private"

# ===== الموقع =====
SITE_URL = "https://qamarsyria.github.io/qamar-malak/"

# ===== توزيع المصادر (7 أيام) =====
SOURCE_ROTATION = ["riyad", "faq", "riyad", "hadith", "riyad", "faq", "hadith"]
