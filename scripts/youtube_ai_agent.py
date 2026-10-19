# scripts/youtube_ai_agent.py (النسخة المحدثة)
from __future__ import annotations
import base64, hashlib, json, os, re, sys, time
from pathlib import Path
import requests, pyotp
from playwright.sync_api import sync_playwright

# استيراد مدير البروكسي الجديد
sys.path.insert(0, str(Path(__file__).parent))
try:
    from proxy_manager import get_working_proxy
    HAS_PROXY_MANAGER = True
except ImportError:
    HAS_PROXY_MANAGER = False
    def get_working_proxy(): return None

def _log(msg: str) -> None:
    print(f"[ai-agent] {msg}", flush=True)

# ... (باقي الدوال: FlightRecorder, BotMemory, GeminiPool, discover_models, ... تبقى كما هي) ...

# دالة التوجيه عبر الجوال (كما هي في النسخة السابقة)
def _navigate_to_upload_mobile(page) -> bool:
    # ... الكود الخاص بك ...
    return True

# دالة OTP (كما هي)
def _handle_otp(page) -> bool:
    # ... الكود الخاص بك ...
    return False

# دالة التنفيذ (كما هي)
def _execute_smart(page, decision: dict, screenshot_dir: str, step: int, memory: BotMemory) -> str:
    # ... الكود الخاص بك ...
    return "unknown"

def _parse_cookies(raw: str) -> list[dict]:
    # ... الكود الخاص بك ...
    pass

def upload_video_ai(...) -> bool:
    # ...
    # ═══ التعديل الرئيسي هنا ═══
    # بدلاً من إعداد VPN، نطلب بروكسي من المدير الذكي
    proxy_config = None
    if HAS_PROXY_MANAGER:
        _log("🔍 البحث عن بروكسي تلقائي...")
        proxy_server = get_working_proxy()
        if proxy_server:
            proxy_config = {"server": proxy_server}
            _log(f"🌐 سأستخدم البروكسي: {proxy_server}")
        else:
            _log("⚠️ لم يتم العثور على بروكسي، سأحاول بدون بروكسي.")
    
    with sync_playwright() as p:
        # تمرير إعدادات البروكسي إلى المتصفح
        launch_args = {"headless": headless, "args": ["--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors"]}
        if proxy_config:
            launch_args["proxy"] = proxy_config
        
        # ... (باقي دالة التشغيل، بما في ذلك اختيار الجهاز، إلخ) ...
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(  # ...
            # ...
        )
        # ...
