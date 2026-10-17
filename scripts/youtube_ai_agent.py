"""رفع فيديو على يوتيوب — AI Vision + Stealth + محاكاة جوال."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

# ═══ استيراد مدير البروكسي ═══
sys.path.insert(0, str(Path(__file__).parent))
try:
    from syrian_proxy import find_working_proxy
    HAS_PROXY = True
except ImportError as e:
    print(f"⚠️  لم أستطع استيراد syrian_proxy: {e}", flush=True)
    HAS_PROXY = False
    def find_working_proxy(): return None


def _log(msg: str) -> None:
    print(f"[ai-agent] {msg}", flush=True)


# ═══════════════════════════════════════════════════════
#  إدارة مفاتيح Gemini
# ═══════════════════════════════════════════════════════

class GeminiPool:
    def __init__(self):
        raw = os.getenv("GEMINI_API_KEYS", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
        self.keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not self.keys:
            raise RuntimeError("لا مفاتيح Gemini")
        self.idx = 0
        self.cooldowns: dict[str, float] = {}
        _log(f"🔑 {len(self.keys)} مفتاح")

    def get(self) -> str:
        now = time.time()
        for _ in range(len(self.keys)):
            k = self.keys[self.idx]
            if now >= self.cooldowns.get(k, 0):
                return k
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
    if _pool is None:
        _pool = GeminiPool()
    return _pool


# ═══════════════════════════════════════════════════════
#  اكتشاف الموديلات تلقائياً
# ═══════════════════════════════════════════════════════

_MODEL_CACHE: list[str] | None = None

def _rank_model(name: str) -> tuple:
    n = name.lower()
    if any(x in n for x in ["embedding", "aqa", "imagen", "veo", "tts", "audio", "vision-latest", "video", "learnlm", "gemma"]):
        return (999, 999, name)
    version = 0.0
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    if m: version = float(m.group(1))
    if "flash" in n and "lite" not in n: kind_rank = 0
    elif "flash" in n and "lite" in n: kind_rank = 1
    elif "pro" in n: kind_rank = 2
    else: kind_rank = 3
    stable = 0
    if "preview" in n or "exp" in n or "experimental" in n: stable = 1
    return (kind_rank, stable, -version, name)

def discover_models(force_refresh: bool = False) -> list[str]:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None and not force_refresh:
        return _MODEL_CACHE

    p = pool()
    key = p.get()
    _log("🔍 نسأل Gemini عن الموديلات المتاحة...")
    try:
        r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=30)
    except Exception:
        return ["gemini-3.8-flash", "gemini-2.5-flash-latest", "gemini-flash-latest"]

    if r.status_code != 200:
        return ["gemini-3.8-flash", "gemini-2.5-flash-latest", "gemini-flash-latest"]

    data = r.json()
    models_raw = data.get("models", [])
    _log(f"📋 وجدنا {len(models_raw)} موديل إجمالي")

    candidates = []
    for m in models_raw:
        name = m.get("name", "").replace("models/", "")
        if "generateContent" in m.get("supportedGenerationMethods", []):
            candidates.append(name)

    _log(f"🎯 منها {len(candidates)} تدعم generateContent")
    if not candidates:
        return ["gemini-3.8-flash", "gemini-2.5-flash-latest"]

    candidates.sort(key=_rank_model)
    top = candidates[:10]
    _log("✅ ترتيبنا النهائي:")
    for i, m in enumerate(top):
        _log(f"   {i + 1}. {m}")

    _MODEL_CACHE = top
    return top


# ═══════════════════════════════════════════════════════
#  استخراج عناصر الصفحة
# ═══════════════════════════════════════════════════════

JS_COLLECT_INTERACTIVE = r"""
() => {
    const results = [];
    const seen = new Set();
    const selectors = ['button','a','input','textarea','select','[role="button"]','[role="radio"]','[role="checkbox"]','[role="tab"]','[role="menuitem"]','[role="option"]','tp-yt-paper-radio-button','ytcp-button','ytcp-radio-button','tp-yt-paper-item','tp-yt-paper-button','[contenteditable="true"]'];
    const all = document.querySelectorAll(selectors.join(','));
    let idx = 0;
    for (const el of all) {
        try {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (rect.width < 3 || rect.height < 3 || style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) < 0.1) continue;
            if (rect.top < 0 || rect.left < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) continue;
            let text = (el.innerText || el.textContent || '').trim().slice(0, 120) || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || '';
            const key = `${el.tagName}|${text}|${Math.round(rect.top)}|${Math.round(rect.left)}`;
            if (seen.has(key)) continue;
            seen.add(key);
            results.push({
                id: idx++, tag: el.tagName.toLowerCase(), text: text, aria: el.getAttribute('aria-label') || '',
                name: el.getAttribute('name') || '', type: el.getAttribute('type') || '',
                disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
                checked: el.checked === true, x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2),
                w: Math.round(rect.width), h: Math.round(rect.height)
            });
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


# ═══════════════════════════════════════════════════════
#  Gemini Vision
# ═══════════════════════════════════════════════════════

SYSTEM_PROMPT = """أنت مساعد أتمتة رفع الفيديوهات على YouTube Studio.
مهمتك: تنظر إلى لقطة شاشة + قائمة العناصر القابلة للتفاعل، وتقرر الخطوة التالية لرفع فيديو.
**المسار:**
1. Details: اكتب العنوان والوصف
2. Video elements: تخطَّ
3. Checks: تخطَّ
4. Made for Kids: اختر "ليس للأطفال" (NOT_MFK)
5. Visibility: اختر "Public"
6. اضغط "Next" حتى الشاشة الأخيرة
7. اضغط "Publish"
**قواعد:**
- لا تختر "made for kids" أبداً.
- إذا شفت "Next" مفعّل → اضغطه.
- إذا شفت "Public" غير محدد → حدده.
- إذا شفت "Publish" مفعّل → اضغطه.
- إذا الشاشة لا تحتاج فعل → {"action": "wait"}
- إذا انتهى الرفع بنجاح → {"action": "done"}
**الإخراج:** JSON فقط، مثل: {"action": "click", "id": 5} أو {"action": "type", "id": 2, "text": "..."}
"""

def _call_gemini(model: str, body: dict, key: str) -> tuple[bool, dict | str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        r = requests.post(url, json=body, timeout=90)
        if r.status_code == 429: return False, "rate_limit"
        if r.status_code == 404: return False, "model_not_found"
        if r.status_code != 200: return False, f"http_{r.status_code}: {r.text[:200]}"
        data = r.json()
        cands = data.get("candidates") or []
        if not cands: return False, "no_candidates"
        txt = "".join(p["text"] for p in (cands[0].get("content") or {}).get("parts") or [] if "text" in p).strip()
        m = re.search(r'\{[\s\S]*\}', txt)
        if not m: return False, f"no_json: {txt[:200]}"
        return True, json.loads(m.group(0))
    except Exception as e:
        return False, f"exception: {str(e)[:120]}"

def _gemini_decide(page, step: int, history: list[str]) -> dict:
    elements = _collect_elements(page)
    img_b64 = _screenshot_b64(page)
    elements_txt = "\n".join([
        f"[{e['id']}] <{e['tag']}> \"{e['text'][:60]}\" name={e['name']} type={e['type']} disabled={e['disabled']} checked={e['checked']} @({e['x']},{e['y']}) {e['w']}x{e['h']}"
        for e in elements
    ])
    recent_history = "\n".join(history[-6:]) if history else "(لا شيء بعد)"
    prompt = f"**الخطوة: {step}**\n**السجل:**\n{recent_history}\n**العناصر ({len(elements)}):**\n{elements_txt}\nما هي الخطوة التالية؟ أجب بـ JSON فقط."
    body = {
        "contents": [{"parts": [{"text": SYSTEM_PROMPT}, {"text": prompt}, {"inlineData": {"mimeType": "image/png", "data": img_b64}}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500, "responseMimeType": "application/json"},
    }
    p = pool()
    models = discover_models()
    last_error = ""
    for model in models:
        for attempt in range(len(p.keys)):
            key = p.get()
            ok, result = _call_gemini(model, body, key)
            if ok:
                result["_elements"] = elements
                result["_model"] = model
                return result
            err = str(result)
            last_error = f"{model}: {err}"
            if "rate_limit" in err: p.fail(60); continue
            elif "model_not_found" in err:
                if _MODEL_CACHE and model in _MODEL_CACHE: _MODEL_CACHE.remove(model)
                break
            else: p.fail(20); continue
    raise RuntimeError(f"فشلت كل الموديلات: {last_error}")


# ═══════════════════════════════════════════════════════
#  تنفيذ القرار
# ═══════════════════════════════════════════════════════

def _execute(page, decision: dict, screenshot_dir: str, step: int) -> str:
    action = decision.get("action", "wait")
    elements = decision.get("_elements", [])
    def find_el(eid):
        for e in elements:
            if e["id"] == eid: return e
        return None

    if action == "done": _log("✅ Gemini أعلن الانتهاء"); return "done"
    if action == "fail":
        reason = decision.get("reason", "?")
        _log(f"❌ Gemini أعلن الفشل: {reason}")
        return f"fail: {reason}"
    if action == "wait":
        sec = int(decision.get("seconds", 3)); _log(f"⏸️  انتظار {sec}ث"); page.wait_for_timeout(sec * 1000); return "wait"
    if action == "click":
        eid = decision.get("id")
        el = find_el(eid)
        if not el: _log(f"⚠️  عنصر {eid} غير موجود"); return f"err: element {eid} not found"
        _log(f"👆 ضغط [{eid}] '{el['text'][:50]}' @({el['x']},{el['y']})")
        clicked = False
        try: page.mouse.click(el["x"], el["y"]); clicked = True
        except Exception as e: _log(f"   ⚠️  mouse: {e}")
        if not clicked and el.get("name"):
            try: page.locator(f'[name="{el["name"]}"]').first.click(force=True, timeout=5000); clicked = True
            except Exception: pass
        page.wait_for_timeout(2500)
        page.screenshot(path=f"{screenshot_dir}/ai_step_{step}_after.png")
        return f"clicked '{el['text'][:40]}'"
    if action == "type":
        eid = decision.get("id")
        text = decision.get("text", "")
        el = find_el(eid)
        if not el: return f"err: element {eid} not found"
        _log(f"⌨️  كتابة [{eid}]: '{text[:50]}'")
        try:
            page.mouse.click(el["x"], el["y"]); page.wait_for_timeout(500)
            page.keyboard.press("Control+A"); page.keyboard.press("Delete"); page.wait_for_timeout(200)
            page.keyboard.type(text, delay=15); page.wait_for_timeout(800)
        except Exception as e: return f"err: {e}"
        return f"typed '{text[:30]}'"
    return "unknown"


# ═══════════════════════════════════════════════════════
#  الكوكيز والمسار البشري
# ═══════════════════════════════════════════════════════

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
    if isinstance(data, dict):
        return [{"name": k, "value": v, "domain": ".youtube.com", "path": "/"} for k, v in data.items()]
    raise ValueError("صيغة غير معروفة")

def _navigate_like_human(page, screenshot_dir: str) -> bool:
    _log("🌐 فتح youtube.com الرئيسية...")
    page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    page.screenshot(path=f"{screenshot_dir}/01_home.png")

    if "signin" in page.url.lower() or "accounts.google" in page.url.lower():
        raise RuntimeError("❌ فشل تسجيل الدخول")

    _log("✅ مسجّل — نبدأ سلوك بشري")
    page.mouse.move(200, 400); page.wait_for_timeout(700)
    page.mouse.move(180, 350); page.wait_for_timeout(500)
    page.mouse.move(300, 500); page.wait_for_timeout(600)

    try:
        page.mouse.wheel(0, 300); page.wait_for_timeout(1500)
        page.mouse.wheel(0, -200); page.wait_for_timeout(1000)
    except Exception: pass

    _log("👤 ضغط على صورة الحساب...")
    avatar_clicked = False
    for sel in ['button#avatar-btn', 'yt-img-shadow#avatar', '#avatar-btn', 'ytd-topbar-menu-button-renderer #avatar-btn', 'button[aria-label*="الحساب"]', 'button[aria-label*="Account"]']:
        try: page.click(sel, timeout=5000); _log(f"   ✅ نجح: {sel}"); avatar_clicked = True; break
        except Exception: continue

    if not avatar_clicked:
        _log("⚠️  ما لقيت صورة الحساب — نروح مباشرة للرفع")
        page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000); return True

    page.wait_for_timeout(2500)
    page.screenshot(path=f"{screenshot_dir}/02_avatar_menu.png")

    _log("➕ البحث عن زر الرفع...")
    create_clicked = False
    for sel in ['ytd-compact-link-renderer a[href="/upload"]', 'a[href="/upload"]', 'yt-formatted-string:has-text("رفع فيديو")', 'yt-formatted-string:has-text("Upload video")', 'tp-yt-paper-item:has-text("رفع فيديو")', 'tp-yt-paper-item:has-text("Upload video")', 'tp-yt-paper-item:has-text("رفع")', 'tp-yt-paper-item:has-text("Upload")']:
        try: page.click(sel, timeout=5000); _log(f"   ✅ نجح: {sel}"); create_clicked = True; break
        except Exception: continue

    if not create_clicked:
        _log("⚠️  ما لقيت زر الرفع — نروح مباشرة")
        page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded", timeout=60000)

    page.wait_for_timeout(8000)
    page.screenshot(path=f"{screenshot_dir}/03_after_create.png")
    _log(f"📍 الرابط: {page.url}")
    return True


# ═══════════════════════════════════════════════════════
#  الدالة الرئيسية
# ═══════════════════════════════════════════════════════

def upload_video_ai(
    video_path: str, title: str, description: str, cookies_raw: str,
    headless: bool = True, screenshot_dir: str = "out/screenshots", max_steps: int = 40,
) -> bool:
    video_path = str(Path(video_path).resolve())
    if not Path(video_path).exists(): raise FileNotFoundError(video_path)

    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
    cookies_list = _parse_cookies(cookies_raw)
    _log(f"🍪 {len(cookies_list)} كوكي")

    _log("═" * 50)
    models = discover_models()
    if not models: raise RuntimeError("❌ لا موديلات")
    _log(f"🎯 سأستخدم: {models[0]}")

    _log("═" * 50)
    proxy_config = None
    if HAS_PROXY:
        try: proxy_config = find_working_proxy()
        except Exception as e: _log(f"⚠️  فشل مدير البروكسي: {e}")

    with sync_playwright() as p:
        # ═══ محاكاة جهاز جوال حقيقي (Pixel 5) ═══
        device = p.devices['Pixel 5']
        
        launch_kwargs = {
            "headless": headless,
            "args": ["--no-sandbox", "--disable-dev-shm-usage"],
        }
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config
            _log(f"🌐 سأرفع عبر بروكسي: {proxy_config.get('server')}")
        else:
            _log("⚠️  بدون بروكسي — IP افتراضي + محاكاة جوال")

        browser = p.chromium.launch(**launch_kwargs)
        
        context = browser.new_context(
            **device,
            locale="ar-SA",
            timezone_id="Asia/Damascus",
        )
        context.add_cookies(cookies_list)
        page = context.new_page()

        # ═══ تفعيل التخفي ═══
        stealth_sync(page)
        _log("🛡️  تم تفعيل وضع التخفي (Stealth)")

        history: list[str] = []
        try:
            _navigate_like_human(page, screenshot_dir)

            _log(f"📁 رفع: {Path(video_path).name}")
            file_input = None
            for _ in range(10):
                c = page.locator('input[type="file"]')
                if c.count() > 0: file_input = c.first; break
                page.wait_for_timeout(3000)

            if file_input is None: raise RuntimeError("❌ ما لقيت input الفيديو")

            file_input.set_input_files(video_path)
            _log("⏳ انتظار 30 ثانية لبدء المعالجة...")
            page.wait_for_timeout(30000)
            page.screenshot(path=f"{screenshot_dir}/04_uploaded.png")

            _log("═" * 50)
            _log("🧠 حلقة Gemini Vision")
            _log(f"📝 العنوان: {title}")
            history.append(f"العنوان: {title}")
            history.append(f"الوصف: {description[:200]}")

            done = False
            for step in range(1, max_steps + 1):
                _log(f"\n{'─' * 50}")
                _log(f"🎯 خطوة {step}")
                page.screenshot(path=f"{screenshot_dir}/ai_step_{step}_before.png")

                try: decision = _gemini_decide(page, step, history)
                except Exception as e: _log(f"❌ فشل Gemini: {e}"); break

                action = decision.get("action")
                model_used = decision.get("_model", "?")
                _log(f"📋 القرار ({model_used}): {action}")

                result = _execute(page, decision, screenshot_dir, step)
                history.append(f"خطوة {step}: {action} → {result}")

                if result == "done": done = True; break
                if result.startswith("fail"): break
                page.wait_for_timeout(2000)

            page.screenshot(path=f"{screenshot_dir}/ai_final.png")
            if done: _log("🎉 اكتمل الرفع"); return True
            _log("⚠️  الحلقة انتهت بدون تأكيد"); return False

        except Exception as e:
            try: page.screenshot(path=f"{screenshot_dir}/ai_error.png")
            except Exception: pass
            _log(f"❌ {e}")
            raise
        finally:
            browser.close()


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
