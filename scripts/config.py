"""كل الإعدادات في مكان واحد."""
import os

# ============================================================
# الفيديو
# ============================================================
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

# ============================================================
# الصوت
# ============================================================
VOICE_ENGINE_PRIORITY = ["gemini", "azure", "piper"]
GEMINI_VOICE = "Charon"
GEMINI_VOICE_FEMALE = "Kore"

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")
AZURE_VOICE = "ar-EG-ShakirNeural"
AZURE_RATE = "-10%"

PIPER_MODEL = "/tmp/piper/ar_JO-kareem-medium.onnx"
PIPER_CONFIG = "/tmp/piper/ar_JO-kareem-medium.onnx.json"
PIPER_LENGTH_SCALE = 1.0

TTS_VOICE = GEMINI_VOICE
TTS_VOICE_FEMALE = GEMINI_VOICE_FEMALE
TTS_RATE = "-10%"
TTS_VOLUME = "+0%"
TTS_DELAY_BETWEEN_SEGMENTS = 3

TARGET_SAMPLE_RATE = "44100"
TARGET_CHANNELS = "1"
TARGET_BITRATE = "192k"

# ============================================================
# القرآن (everyayah)
# ============================================================
QURAN_RECITER = "Alafasy_128kbps"
STORY_RECITER = "Abdurrahmaan_As-Sudais_192kbps"
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
# الخلفيات
# ============================================================
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_QUERY = "mosque,islamic,nature,sky,clouds,desert,stars"
BG_FALLBACK = "assets/bg/default.jpg"
BG_OVERLAY_OPACITY = 0.45
KEN_BURNS_ZOOM = 1.05

BACKGROUNDS_DIR = "assets/backgrounds"
BG_IMAGES_DIR = "assets/backgrounds/images"
BG_VIDEOS_DIR = "assets/backgrounds/videos"

# ============================================================
# المسارات
# ============================================================
FAQ_JSON = "data/faq-data.json"
HADITH_JSON = "data/hadith-db.json"
RIYAD_JSON = "data/riyad-salihin.json"
RUQYAH_JSON = "data/ruqyah/ruqyah.json"
STORIES_DIR = "data/stories"
STATE_FILE = "state/published.json"
FONT_DIR = "assets/fonts"
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
# Freesound API (المؤثرات الصوتية)
# ============================================================
FREESOUND_API_KEY = "mctCOs0Xtos88jMekujyo8r9e0dDenmKAOhjiGzD"
FREESOUND_CACHE_DIR = "assets/audio/sfx_cache"

# ============================================================
# خريطة المشاهد (الخلفية + المؤثرات الصوتية)
# ============================================================
SCENE_MAP = {
    "conspiracy": {
        "bg_query": "dark forest fog",
        "color_tint": (20, 30, 60, 100),
        "sfx_queries": ["whisper", "wind", "owl hoot"]
    },
    "fear": {
        "bg_query": "storm sky dark clouds",
        "color_tint": (40, 40, 50, 120),
        "sfx_queries": ["rain", "thunder distant", "wind strong"]
    },
    "sadness": {
        "bg_query": "rain autumn leaves",
        "color_tint": (60, 70, 90, 80),
        "sfx_queries": ["sad bird", "stream water", "light rain"]
    },
    "calm": {
        "bg_query": "peaceful nature sunrise",
        "color_tint": (200, 180, 100, 20),
        "sfx_queries": ["birds chirping", "gentle breeze", "water spring"]
    },
    "battle": {
        "bg_query": "desert sand dunes",
        "color_tint": (100, 50, 20, 80),
        "sfx_queries": ["sword clash", "horse gallop", "battle cry"]
    },
    "royal": {
        "bg_query": "ancient stone palace architecture",
        "color_tint": (150, 100, 50, 80),
        "sfx_queries": ["crowd distant", "wind light", "stone echo"]
    },
    "night": {
        "bg_query": "starry night sky desert",
        "color_tint": (10, 10, 40, 100),
        "sfx_queries": ["crickets night", "gentle wind", "owl"]
    },
    "sea": {
        "bg_query": "ocean waves horizon",
        "color_tint": (30, 60, 100, 80),
        "sfx_queries": ["ocean waves", "seagulls", "wind sea"]
    }
}

# ============================================================
# الهاشتاجات
# ============================================================
HASHTAGS = "#إسلاميات #قرآن #حديث #قصص_الأنبياء #رقية_شرعية #دين #تذكير #قمر_الشام #ملاك_المعرفة"

# ============================================================
# تيليجرام، فيسبوك، يوتيوب
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
FACEBOOK_PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_TOKEN", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")
YT_PRIVACY = "public"

# ============================================================
# الموقع
# ============================================================
SITE_URL = "https://qamarsyria.github.io/qamar-malak/"

# ============================================================
# توزيع المصادر اليومية
# ============================================================
SOURCE_ROTATION = [
    "riyad", "story", "faq", "hadith", "story", "faq", "hadith",
]

# ============================================================
# إعدادات القصص
# ============================================================
STORIES_AVAILABLE = ["yusuf", "adam", "nuh", "ibrahim", "musa"]

YUSUF_EPISODES = [
    (1, 3), (4, 6), (7, 10), (11, 14), (15, 18), (19, 22), (23, 29),
    (30, 35), (36, 42), (43, 49), (50, 53), (54, 57), (58, 62), (63, 68),
    (69, 76), (77, 82), (83, 87), (88, 92), (93, 98), (99, 101), (102, 111),
]

EPISODE_MIN_DURATION = 600
EPISODE_MAX_DURATION = 1200

# ============================================================
# إعدادات شرعية
# ============================================================
FORBIDDEN_VISUALS = [
    "prophets", "companions", "mothers_of_believers", "ahl_al_bayt", "angels", "khalifa_rashidun",
]

QURAN_RULES = {
    "no_music": True, "no_sfx": True, "no_cut": True, "complete_ayah_only": True,
}

# ============================================================
# التوجيه العاطفي
# ============================================================
STYLE_HINTS = {
    "hadith":  "(in a calm, solemn, and dignified tone)",
    "faq":     "(in a clear, educational, and friendly tone)",
    "riyad":   "(in a spiritual, moving, and humble tone)",
    "ruqyah":  "(in a strong, firm, and authoritative tone)",
    "story":   "(in a narrative, engaging, and warm tone, with slight pauses between sentences)",
}

# ============================================================
# قص الصمت
# ============================================================
TRIM_SILENCE_ENABLED = True
TRIM_SILENCE_THRESHOLD = "-40dB"
TRIM_SILENCE_MIN_LENGTH = 0.3
