"""رفع فيديو على يوتيوب — Mobile Web Interface (User's Hardcoded Path)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import pyotp
from playwright.sync_api import sync_playwright

# ═══ استيراد الملفات المحلية ═══
sys.path.insert(0, str(Path(__file__).parent))
try:
    from syrian_proxy import find_working_proxy
    HAS_PROXY = True
except ImportError:
    HAS_PROXY = False
    def find_working_proxy(): return None

def _log(msg: str) -> None:
    print(f"[ai-agent] {msg}", flush=True)

# ═══════════════════════════════════════════════════════
#  الصندوق الأسود (Flight Recorder)
# ═══════════════════════════════════════════════════════
class FlightRecorder:
    def __init__(self, output_dir: str):
        self.filepath = Path(output_dir) / "flight_recorder.json"
        self.records = []
    def record(self, step: int, action: str, details: str, result: str):
        self.records.append({"timestamp": time.strftime("%H:%M:%S"), "step": step, "action": action, "details": details, "result": result})
        try: self.filepath.write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception: pass

recorder: FlightRecorder | None = None

def get_totp_code() -> str | None:
    secret = os.getenv("GOOGLE_TOTP_SECRET", "").strip().replace(" ", "")
    if not secret: return None
    try: return pyotp.TOTP(secret).now()
    except Exception: return None

# ═══════════════════════════════════════════════════════
#  🧠 الحالة الذكية للبوت
# ═══════════════════════════════════════════════════════
class BotMemory:
    def __init__(self):
        self.last_action = None
        self.same_hash_count = 0
        self.failed_actions = []

    def get_feedback(self) -> str:
        parts = []
        if self.last_action:
            if self.same_hash_count >= 1:
                parts.append(f"⚠️ الإجراء السابق ({self.last_action}) لم يغيّر الشاشة!")
            else:
                parts.append(f"✅ الإجراء السابق ({self.last_action}) نجح")
        if self.same_hash_count >= 2:
            parts.append(f"🚫 تحذير: الشاشة لم تتغير لـ {self.same_hash_count} خطوات.")
        if self.failed_actions:
            parts.append(f"❌ إجراءات فشلت: {', '.join(self.failed_actions[-3:])}")
        return "\n".join(parts) if parts else "(لا شيء)"

def _hash_screenshot(page) -> str:
    try:
        png = page.screenshot(type="png", full_page=False)
        return hashlib.md5(png).hexdigest()[:16]
    except Exception:
        return "error"

# ═══════════════════════════════════════════════════════
#  إدارة مفاتيح Gemini
# ═══════════════════════════════════════════════════════
class GeminiPool:
    def __init__(self):
        raw = os.getenv("GEMINI_API_KEYS", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
        self.keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not self.keys: raise RuntimeError("لا مفاتيح Gemini")
        self.idx = 0
        self.cooldowns: dict[str, float] = {}
        _log(f"🔑 {len(self.keys)} مفتاح")
    def get(self) -> str:
        now = time.time()
        for _ in range(len(self.keys)):
            k = self.keys[self.idx]
            if now >= self.cooldowns.get(k, 0): return k
            self.idx = (self.idx + 1) % len(self.keys)
        wait = max(1, int(min(self.cooldowns.values()) - time.time()))
        time.sleep(wait)
        return self.get()
    def fail(self, sec: int = 60):
        k = self.keys[self.idx]
        self.cooldowns[k] = time.time() + sec
        self.idx = (self.idx + 1) % len(self.keys)

_pool: GeminiPool | None = None
def pool() -> GeminiPool:
    global _pool
    if _pool is None: _pool = GeminiPool()
    return _pool

_MODEL_CACHE: list[str] | None = None

def _rank_model(name: str) -> tuple:
    n = name.lower()
    if any(x in n for x in ["embedding", "aqa", "imagen", "veo", "tts", "audio", "vision-latest", "video", "learnlm", "gemma"]): return (999, 999, name)
    version = 0.0
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    if m: version = float(m.group(1))
    if "flash" in n and "lite" not in n: kind_rank = 0
    elif "flash" in n and "lite" in n: kind_rank = 1
    elif "pro" in n: kind_rank = 2
    else: kind_rank = 3
    stable = 1 if any(x in n for x in ["preview", "exp", "experimental"]) else 0
    return (kind_rank, stable, -version, name)

def discover_models(force_refresh: bool = False) -> list[str]:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None and not force_refresh: return _MODEL_CACHE
    p = pool(); key = p.get()
    try:
        r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=30)
        if r.status_code != 200: return ["gemini-3.8-flash"]
        candidates = [m.get("name", "").replace("models/", "") for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        if not candidates: return ["gemini-3.8-flash"]
        candidates.sort(key=_rank_model)
        _MODEL_CACHE = candidates[:10]
        return _MODEL_CACHE
    except Exception: return ["gemini-3.8-flash"]

def _screenshot_b64(page) -> str:
    return base64.b64encode(page.screenshot(type="png", full_page=False)).decode()

SYSTEM_PROMPT = """أنت مساعد أتمتة رفع الفيديوهات على واجهة الجوال لموقع يوتيوب (m.youtube.com).
أنت ترى لقطة شاشة وتتخذ قراراً.
**أنت الآن في شاشة رفع الفيديو (Upload Dialog).**
1. إذا ظهرت شاشة التفاصيل: اكتب العنوان والوصف.
2. اضغط "التالي" أو "Next" حتى تصل لزر "نشر" (Publish).
3. اضغط "نشر".

**قواعد:**
- إذا كان هناك شريط تحميل، استخدم {"action": "wait_long", "seconds": 20}
- الإخراج: JSON فقط، مثل: {"action": "click", "x": 500, "y": 600} أو {"action": "type", "x": 500, "y": 300, "text": "..."} أو {"action": "done"}.
"""

def _call_gemini(model: str, body: dict, key: str) -> tuple[bool, dict | str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        r = requests.post(url, json=body, timeout=90)
        if r.status_code == 429: return False, "rate_limit"
        if r.status_code == 404: return False, "model_not_found"
        if r.status_code != 200: return False, f"http_{r.status_code}: {r.text[:200]}"
        data = r.json(); cands = data.get("candidates") or []
        if not cands: return False, "no_candidates"
        txt = "".join(p["text"] for p in (cands[0].get("content") or {}).get("parts") or [] if "text" in p).strip()
        m = re.search(r'\{[\s\S]*\}', txt)
        if not m: return False, f"no_json: {txt[:200]}"
        return True, json.loads(m.group(0))
    except Exception as e: return False, f"exception: {str(e)[:120]}"

def _gemini_decide(page, step: int, memory: BotMemory) -> dict:
    img_b64 = _screenshot_b64(page)
    feedback = memory.get_feedback()
    
    context = f"**الخطوة:** {step}\n**التغذية الراجعة:** {feedback}\n**السؤال:** ما الإجراء التالي؟"
    
    body = {"contents": [{"parts": [{"text": SYSTEM_PROMPT}, {"text": context}, {"inlineData": {"mimeType": "image/png", "data": img_b64}}]}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300, "responseMimeType": "application/json"}}
    
    p = pool(); models = discover_models(); last_error = ""
    for model in models:
        for attempt in range(len(p.keys)):
            key = p.get(); ok, result = _call_gemini(model, body, key)
            if ok: 
                result["_model"] = model
                return result
            err = str(result); last_error = f"{model}: {err}"
            if "rate_limit" in err: p.fail(60); continue
            elif "model_not_found" in err:
                if _MODEL_CACHE and model in _MODEL_CACHE: _MODEL_CACHE.remove(model)
                break
            else: p.fail(20); continue
    raise RuntimeError(f"فشلت كل الموديلات: {last_error}")

# ═══════════════════════════════════════════════════════
#  دالة OTP الذكية
# ═══════════════════════════════════════════════════════
def _handle_otp(page) -> bool:
    try:
        url = page.url.lower()
        body_text = page.locator("body").inner_text(timeout=2000).lower()
        
        is_otp_screen = (
            "accounts.google.com" in url or 
            "verify it's you" in body_text or 
            "رمز التحقق" in body_text or 
            "ادخل الرمز" in body_text or
            "Enter the code" in body_text
        )

        if not is_otp_screen: return False

        has_input = page.evaluate("""() => document.querySelectorAll('input[type="text"], input[type="tel"]').length > 0""")
        if has_input:
            _log("🔐 [خارق] اكتشفت شاشة تحقق حقيقية — سأستخدم TOTP...")
            code = get_totp_code()
            if code:
                _log(f"🔑 كود TOTP: {code}")
                page.evaluate("""() => {
                    const inputs = document.querySelectorAll('input[type="text"], input[type="tel"]');
                    if (inputs.length > 0) { inputs[0].focus(); return true; }
                    return false;
                }""")
                page.keyboard.type(code, delay=100)
                page.wait_for_timeout(1000)
                page.keyboard.press("Enter")
                page.wait_for_timeout(3000)
                return True
    except Exception:
        pass
    return False

# ═══════════════════════════════════════════════════════
#  🎯 المسار اليدوي المبرمج (حسب صور الدكتور)
# ═══════════════════════════════════════════════════════
def _navigate_to_upload_mobile(page) -> bool:
    try:
        # 1. الضغط على "أنت" (أسفل اليمين)
        _log("📱 [مسار] 1. الضغط على 'أنت'...")
        you_btn = page.locator('a[href*="feed/you"], text="أنت", text="You"').first
        if you_btn.count() > 0 and you_btn.is_visible():
            you_btn.click(force=True)
            page.wait_for_timeout(3000)

        # 2. الضغط على "مشاهدة القناة"
        _log("📱 [مسار] 2. الضغط على 'مشاهدة القناة'...")
        view_ch_btn = page.locator('text="مشاهدة القناة", text="View channel"').first
        if view_ch_btn.count() > 0 and view_ch_btn.is_visible():
            view_ch_btn.click(force=True)
            page.wait_for_timeout(4000)

        # 3. الضغط على زر '+' (الإنشاء) في الأسفل
        _log("📱 [مسار] 3. الضغط على زر '+' (إنشاء)...")
        plus_btn = page.locator('button[aria-label*="إنشاء"], button[aria-label*="Create"], c3-icon[icon-name="add"]').first
        if plus_btn.count() > 0 and plus_btn.is_visible():
            plus_btn.click(force=True)
            page.wait_for_timeout(3000)
            
        # 4. اختيار "رفع فيديو"
        _log("📱 [مسار] 4. اختيار 'رفع فيديو'...")
        upload_btn = page.locator('text="رفع فيديو", text="Upload video"').first
        if upload_btn.count() > 0 and upload_btn.is_visible():
            upload_btn.click(force=True)
            page.wait_for_timeout(4000)
            return True
            
        return False
    except Exception as e:
        _log(f"⚠️ خطأ في المسار اليدوي: {e}")
        return False

def _execute_smart(page, decision: dict, screenshot_dir: str, step: int, memory: BotMemory) -> str:
    action = decision.get("action", "wait")
    if action == "done": _log("✅ Gemini أعلن الانتهاء"); return "done"
    if action == "fail": _log(f"❌ Gemini أعلن الفشل: {decision.get('reason')}"); return "fail"
    if action == "wait":
        sec = int(decision.get("seconds", 3)); page.wait_for_timeout(sec * 1000); return "wait"
    if action == "wait_long":
        sec = int(decision.get("seconds", 20)); page.wait_for_timeout(sec * 1000); return "wait_long"

    hash_before = _hash_screenshot(page)
    
    if action == "click":
        x, y = decision.get("x", 0), decision.get("y", 0)
        _log(f"👆 نقرة @({x},{y})")
        try: page.mouse.click(x, y); page.wait_for_timeout(3500)
        except Exception as e: return f"err: {e}"
        action_desc = f"click ({x},{y})"
    elif action == "type":
        x, y, text = decision.get("x", 0), decision.get("y", 0), decision.get("text", "")
        _log(f"⌨️  كتابة @({x},{y}): '{text[:40]}'")
        try:
            page.mouse.click(x, y); page.wait_for_timeout(500)
            page.keyboard.press("Control+A"); page.keyboard.press("Delete"); page.wait_for_timeout(200)
            page.keyboard.type(text, delay=15); page.wait_for_timeout(800)
        except Exception as e: return f"err: {e}"
        action_desc = f"type at ({x},{y})"
    else:
        return "unknown"

    page.wait_for_timeout(1000)
    if hash_before == _hash_screenshot(page):
        _log(f"⚠️  [ذكي] لم تتغير الشاشة بعد {action_desc}")
        memory.same_hash_count += 1
        memory.failed_actions.append(action_desc)
        return "no_change"
    else:
        _log(f"✅ [ذكي] الشاشة تغيّرت!")
        memory.same_hash_count = 0
        memory.failed_actions = []
        return "changed"

def _parse_cookies(raw: str) -> list[dict]:
    data = json.loads(raw)
    if isinstance(data, list):
        out = []
        for c in data:
            if not c.get("name") or c.get("value") is None: continue
            item = {"name": c["name"], "value": c["value"], "domain": c.get("domain", ".youtube.com"), "path": c.get("path", "/")}
            if c.get("expirationDate"): item["expires"] = float(c["expirationDate"])
            if c.get("httpOnly") is not None: item["httpOnly"] = bool(c["httpOnly"])
            if c.get("secure") is not None: item["secure"] = bool(c["secure"])
            ss = c.get("sameSite")
            if ss in ("no_restriction", "None"): item["sameSite"] = "None"
            elif ss in ("lax", "Lax"): item["sameSite"] = "Lax"
            elif ss in ("strict", "Strict"): item["sameSite"] = "Strict"
            out.append(item)
        return out
    if isinstance(data, dict): return [{"name": k, "value": v, "domain": ".youtube.com", "path": "/"} for k, v in data.items()]
    raise ValueError("صيغة غير معروفة")

def upload_video_ai(video_path: str, title: str, description: str, cookies_raw: str, headless: bool = True, screenshot_dir: str = "out/screenshots", max_steps: int = 60) -> bool:
    global recorder
    video_path = str(Path(video_path).resolve())
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
    recorder = FlightRecorder(screenshot_dir)
    memory = BotMemory()
    cookies_list = _parse_cookies(cookies_raw)
    proxy_config = None
    if HAS_PROXY:
        try: proxy_config = find_working_proxy()
        except Exception: pass

    with sync_playwright() as p:
        def run_browser(proxy_cfg):
            launch_kwargs = {"headless": headless, "args": ["--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors"]}
            if proxy_cfg: launch_kwargs["proxy"] = proxy_cfg; _log(f"🌐 بروكسي: {proxy_cfg.get('server')}")
            else: _log("⚠️ بدون بروكسي")
            browser = p.chromium.launch(**launch_kwargs)
            
            iphone_13 = p.devices['iPhone 13']
            context = browser.new_context(**iphone_13, locale="ar-SA", timezone_id="Asia/Damascus", ignore_https_errors=True)
            
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)
            
            context.add_cookies(cookies_list)
            page = context.new_page()
            return browser, context, page

        browser = None
        try:
            url = "https://m.youtube.com/"
            if proxy_config:
                try:
                    browser, context, page = run_browser(proxy_config)
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    _log(f"⚠️ بروكسي فشل: {str(e)[:100]}")
                    if browser: browser.close()
                    browser, context, page = run_browser(None)
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
            else:
                browser, context, page = run_browser(None)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

            page.wait_for_timeout(6000)
            if "signin" in page.url.lower() or "accounts.google" in page.url.lower():
                raise RuntimeError("❌ فشل تسجيل الدخول")

            _log("✅ دخلنا واجهة الجوال — البوت الخارق يبدأ")
            
            # 🎯 تطبيق المسار المبرمج
            _navigate_to_upload_mobile(page)
            
            # ننتظر ظهور زر الرفع
            _log("⏳ انتظار شاشة الرفع...")
            file_uploaded = False
            for _ in range(10):
                if page.locator('input[type="file"]').count() > 0:
                    _log("📁 وجدنا حقل الرفع! جاري رفع الملف...")
                    page.locator('input[type="file"]').first.set_input_files(video_path)
                    page.wait_for_timeout(30000)
                    file_uploaded = True
                    break
                page.wait_for_timeout(2000)

            if not file_uploaded:
                _log("⚠️ لم نجد شاشة الرفع، سنستخدم Gemini للبحث...")

            for step in range(1, max_steps + 1):
                _log(f"\n{'═' * 50}\n🎯 خطوة {step}")
                
                if _handle_otp(page):
                    if recorder: recorder.record(step, "handle_otp", "TOTP", "success")
                    continue

                if not file_uploaded and page.locator('input[type="file"]').count() > 0:
                    _log("📁 رفع الفيديو...")
                    page.locator('input[type="file"]').first.set_input_files(video_path)
                    page.wait_for_timeout(30000)
                    file_uploaded = True
                    if recorder: recorder.record(step, "upload_file", "FILE", "success")
                    continue

                try: decision = _gemini_decide(page, step, memory)
                except Exception as e: 
                    _log(f"❌ فشل Gemini: {e}")
                    if recorder: recorder.record(step, "gemini_fail", str(e), "error")
                    break

                action = decision.get("action")
                _log(f"🧠 قرار Gemini ({decision.get('_model', '?')}): {action}")

                result = _execute_smart(page, decision, screenshot_dir, step, memory)
                memory.last_action = action
                
                if recorder: recorder.record(step, action, json.dumps({k:v for k,v in decision.items() if not k.startswith('_')}, ensure_ascii=False), result)

                if result == "done": 
                    _log("🎉 اكتمل الرفع!")
                    return True
                if result.startswith("fail"): break

            page.screenshot(path=f"{screenshot_dir}/failure_state.png")
            _log("⚠️ انتهت المحاولات")
            return False

        except Exception as e:
            _log(f"❌ {e}")
            try: page.screenshot(path=f"{screenshot_dir}/failure_state.png")
            except Exception: pass
            return False
        finally:
            if browser: browser.close()

if __name__ == "__main__":
    cookies_raw = os.getenv("YOUTUBE_COOKIES", "[]")
    ok = upload_video_ai(
        video_path="out/video.mp4",
        title=os.getenv("YT_TITLE", "اختبار AI Agent — مالك فاكتوري"),
        description=os.getenv("YT_DESC", "رفع تجريبي عبر Gemini Vision."),
        cookies_raw=cookies_raw,
        headless=True,
    )
    exit(0 if ok else 1)
