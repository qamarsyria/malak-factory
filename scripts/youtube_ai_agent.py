"""رفع فيديو على يوتيوب — AI Vision + TOTP + Flight Recorder + Direct Upload URL."""
from __future__ import annotations

import base64
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

try:
    from email_reader import get_youtube_verification
    HAS_EMAIL = True
except ImportError:
    HAS_EMAIL = False
    def get_youtube_verification(): return None

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

JS_COLLECT_INTERACTIVE = r"""
() => {
    const results = []; const seen = new Set();
    const selectors = ['button','a','input','textarea','select','[role="button"]','[role="radio"]','[role="checkbox"]','[role="tab"]','[role="menuitem"]','[role="option"]','tp-yt-paper-radio-button','ytcp-button','ytcp-radio-button','tp-yt-paper-item','tp-yt-paper-button','[contenteditable="true"]','div[role="checkbox"]'];
    const all = document.querySelectorAll(selectors.join(','));
    let idx = 0;
    for (const el of all) {
        try {
            const rect = el.getBoundingClientRect(); const style = window.getComputedStyle(el);
            if (rect.width < 3 || rect.height < 3 || style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) < 0.1) continue;
            if (rect.top < 0 || rect.left < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) continue;
            let text = (el.innerText || el.textContent || '').trim().slice(0, 120) || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || '';
            const key = `${el.tagName}|${text}|${Math.round(rect.top)}|${Math.round(rect.left)}`;
            if (seen.has(key)) continue; seen.add(key);
            results.push({ id: idx++, tag: el.tagName.toLowerCase(), text: text, aria: el.getAttribute('aria-label') || '', name: el.getAttribute('name') || '', type: el.getAttribute('type') || '', disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true', checked: el.checked === true, x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2), w: Math.round(rect.width), h: Math.round(rect.height) });
        } catch (e) {}
    }
    return results.slice(0, 80);
}
"""

def _collect_elements(page) -> list[dict]:
    try: return page.evaluate(JS_COLLECT_INTERACTIVE)
    except Exception: return []

def _screenshot_b64(page) -> str:
    return base64.b64encode(page.screenshot(type="png", full_page=False)).decode()

SYSTEM_PROMPT = """أنت مساعد أتمتة رفع الفيديوهات على YouTube Studio.
أنت الآن في شاشة رفع الفيديو (Upload Dialog). 

**ماذا تفعل:**
1. إذا كنت ترى شاشة تطلب منك اختيار وسيلة للتحقق أو شاشة فيها مربع لإدخال كود (6 أرقام): أعلن التوقف فوراً بـ {"action": "get_email_otp"}.
2. إذا ظهرت نافذة "إثبات هويتك" أو "Verify your identity": اضغط على زر "التالي" (Next).
3. إذا ظهرت شاشة التفاصيل (Details): اكتب العنوان والوصف.
4. إذا ظهرت شاشة Video elements: اضغط Next.
5. إذا ظهرت شاشة Checks: اضغط Next.
6. إذا ظهرت شاشة Made for Kids: اختر "ليس للأطفال" (NOT_MFK) ثم اضغط Next.
7. إذا ظهرت شاشة Visibility: اختر "Public" ثم اضغط Next.
8. في الشاشة الأخيرة: اضغط "Publish" أو "نشر".
9. إذا الشاشة لا تحتاج فعل: {"action": "wait"}

**الإخراج:** JSON فقط، بأحد هذه الأشكال:
- {"action": "click", "id": 5}
- {"action": "type", "id": 2, "text": "المحتوى"}
- {"action": "wait", "seconds": 3}
- {"action": "get_email_otp"}
- {"action": "done"}
- {"action": "fail", "reason": "..."}
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

def _gemini_decide(page, step: int, history: list[str]) -> dict:
    elements = _collect_elements(page); img_b64 = _screenshot_b64(page)
    elements_txt = "\n".join([f"[{e['id']}] <{e['tag']}> \"{e['text'][:60]}\" name={e['name']} type={e['type']} disabled={e['disabled']} checked={e['checked']} @({e['x']},{e['y']}) {e['w']}x{e['h']}" for e in elements])
    recent_history = "\n".join(history[-6:]) if history else "(لا شيء بعد)"
    prompt = f"**الخطوة: {step}**\n**السجل:**\n{recent_history}\n**العناصر ({len(elements)}):**\n{elements_txt}\nما هي الخطوة التالية؟ أجب بـ JSON فقط."
    body = {"contents": [{"parts": [{"text": SYSTEM_PROMPT}, {"text": prompt}, {"inlineData": {"mimeType": "image/png", "data": img_b64}}]}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500, "responseMimeType": "application/json"}}
    p = pool(); models = discover_models(); last_error = ""
    for model in models:
        for attempt in range(len(p.keys)):
            key = p.get(); ok, result = _call_gemini(model, body, key)
            if ok: result["_elements"] = elements; result["_model"] = model; return result
            err = str(result); last_error = f"{model}: {err}"
            if "rate_limit" in err: p.fail(60); continue
            elif "model_not_found" in err:
                if _MODEL_CACHE and model in _MODEL_CACHE: _MODEL_CACHE.remove(model)
                break
            else: p.fail(20); continue
    raise RuntimeError(f"فشلت كل الموديلات: {last_error}")

def _execute(page, decision: dict, screenshot_dir: str, step: int) -> str:
    action = decision.get("action", "wait"); elements = decision.get("_elements", [])
    def find_el(eid):
        for e in elements:
            if e["id"] == eid: return e
        return None

    if action == "done": _log("✅ Gemini أعلن الانتهاء"); return "done"
    if action == "fail": _log(f"❌ Gemini أعلن الفشل: {decision.get('reason')}"); return "fail"
    if action == "wait":
        sec = int(decision.get("seconds", 3)); _log(f"⏸️  انتظار {sec}ث"); page.wait_for_timeout(sec * 1000); return "wait"
        
    if action == "get_email_otp":
        _log("🔐 Gemini اكتشف شاشة تحقق! جاري إحضار الكود...")
        code = get_totp_code()
        if code:
            _log(f"🔑 كود TOTP تم توليده: {code} — جاري كتابته...")
            try:
                inputs = page.locator('input[type="text"], input[type="tel"], input:not([type])')
                if inputs.count() > 0:
                    inputs.first.click(force=True); page.keyboard.type(code, delay=100); page.wait_for_timeout(1000); page.keyboard.press("Enter")
                    return "otp_entered"
                else: return "err: no input for otp"
            except Exception as e: return f"err: {e}"
        return "fail: no totp"

    if action == "click":
        eid = decision.get("id"); el = find_el(eid)
        if not el: return f"err: element {eid} not found"
        _log(f"👆 ضغط [{eid}] '{el['text'][:50]}' @({el['x']},{el['y']})")
        clicked = False
        try: page.mouse.click(el["x"], el["y"]); clicked = True
        except Exception as e: _log(f"   ⚠️ mouse: {e}")
            
        if el['text'] in ["التالي", "Next", "Verify", "تأكيد"]:
            _log(f"   ⚡ ضغط زر Enter للتفاعل مع نافذة جوجل...")
            try: page.keyboard.press("Enter"); clicked = True
            except Exception: pass
        page.wait_for_timeout(2500); page.screenshot(path=f"{screenshot_dir}/ai_step_{step}_after.png")
        return f"clicked '{el['text'][:40]}'"
        
    if action == "type":
        eid = decision.get("id"); text = decision.get("text", ""); el = find_el(eid)
        if not el: return f"err: element {eid} not found"
        _log(f"⌨️  كتابة [{eid}]: '{text[:50]}'")
        try:
            page.mouse.click(el["x"], el["y"]); page.wait_for_timeout(500)
            page.keyboard.press("Control+A"); page.keyboard.press("Delete"); page.wait_for_timeout(200)
            page.keyboard.type(text, delay=15); page.wait_for_timeout(800)
        except Exception as e: return f"err: {e}"
        return f"typed '{text[:30]}'"
    return "unknown"

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

def upload_video_ai(video_path: str, title: str, description: str, cookies_raw: str, headless: bool = True, screenshot_dir: str = "out/screenshots", max_steps: int = 50) -> bool:
    global recorder
    video_path = str(Path(video_path).resolve())
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
    recorder = FlightRecorder(screenshot_dir)
    cookies_list = _parse_cookies(cookies_raw)
    proxy_config = None
    if HAS_PROXY:
        try: proxy_config = find_working_proxy()
        except Exception: pass

    with sync_playwright() as p:
        def run_browser(proxy_cfg):
            launch_kwargs = {"headless": headless, "args": ["--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors"]}
            if proxy_cfg: launch_kwargs["proxy"] = proxy_cfg; _log(f"🌐 سأفتح المتصفح عبر بروكسي: {proxy_cfg.get('server')}")
            else: _log("⚠️ بدون بروكسي - اتصال مباشر")
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ar-SA", timezone_id="Asia/Damascus", ignore_https_errors=True, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); window.chrome = { runtime: {} };")
            context.add_cookies(cookies_list)
            page = context.new_page()
            return browser, context, page

        browser = None
        try:
            # ═══ التوجيه المباشر لصفحة الرفع ═══
            url = "https://www.youtube.com/upload"
            if proxy_config:
                try:
                    browser, context, page = run_browser(proxy_config)
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    _log("✅ نجح الاتصال عبر البروكسي")
                except Exception as e:
                    _log(f"⚠️ فشل الاتصال عبر البروكسي: {str(e)[:100]}")
                    if browser: browser.close()
                    _log("🔄 إعادة المحاولة بدون بروكسي...")
                    browser, context, page = run_browser(None)
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
            else:
                browser, context, page = run_browser(None)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

            page.wait_for_timeout(6000)
            if "signin" in page.url.lower() or "accounts.google" in page.url.lower():
                raise RuntimeError("❌ فشل تسجيل الدخول")

            _log("✅ تم الدخول لصفحة الرفع — نبدأ حلقة Gemini")
            file_uploaded = False
            history: list[str] = []

            for step in range(1, max_steps + 1):
                _log(f"\n{'─' * 50}\n🎯 خطوة {step}")
                page.screenshot(path=f"{screenshot_dir}/ai_step_{step}_before.png")

                if not file_uploaded and page.locator('input[type="file"]').count() > 0:
                    _log("📁 وجدنا نافذة الرفع! جاري رفع الملف...")
                    page.locator('input[type="file"]').first.set_input_files(video_path)
                    _log("⏳ انتظار 30 ثانية للمعالجة...")
                    page.wait_for_timeout(30000)
                    file_uploaded = True
                    history.append("تم رفع الفيديو بنجاح")
                    if recorder: recorder.record(step, "upload_file", "FILE", "success")
                    continue

                try: decision = _gemini_decide(page, step, history)
                except Exception as e: 
                    _log(f"❌ فشل Gemini: {e}")
                    if recorder: recorder.record(step, "gemini_fail", str(e), "error")
                    break

                action = decision.get("action"); model = decision.get("_model", "?")
                _log(f"📋 القرار ({model}): {action}")

                result = _execute(page, decision, screenshot_dir, step)
                history.append(f"خطوة {step}: {action} → {result}")
                
                details = json.dumps(decision.get("_elements", [])[:3], ensure_ascii=False)
                if recorder: recorder.record(step, action, details, result)

                if result == "done": 
                    _log("🎉 اكتمل الرفع بنجاح!")
                    return True
                if result.startswith("fail"): break
                
                page.wait_for_timeout(2000)

            page.screenshot(path=f"{screenshot_dir}/ai_final.png")
            _log("⚠️ الحلقة انتهت بدون تأكيد نهائي")
            return False

        except Exception as e:
            _log(f"❌ {e}")
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
