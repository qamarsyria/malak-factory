"""رفع فيديو على يوتيوب باستخدام Gemini Vision كموجّه."""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def _log(msg: str) -> None:
    print(f"[ai-agent] {msg}", flush=True)


# ═══════════════════════════════════════════════════════
#  إدارة مفاتيح Gemini (نفس نمط gemini_tts_pool)
# ═══════════════════════════════════════════════════════

class GeminiPool:
    def __init__(self):
        raw = os.getenv("GEMINI_API_KEYS", "").strip()
        if not raw:
            raw = os.getenv("GEMINI_API_KEY", "").strip()
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
        # كل المفاتيح في تبريد
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
#  أدوات استخراج عناصر الصفحة
# ═══════════════════════════════════════════════════════

JS_COLLECT_INTERACTIVE = r"""
() => {
    const results = [];
    const seen = new Set();
    const selectors = [
        'button', 'a', 'input', 'textarea', 'select',
        '[role="button"]', '[role="radio"]', '[role="checkbox"]',
        '[role="tab"]', '[role="menuitem"]', '[role="option"]',
        'tp-yt-paper-radio-button', 'ytcp-button', 'ytcp-radio-button',
        'tp-yt-paper-item', 'tp-yt-paper-button',
        '[contenteditable="true"]'
    ];
    const all = document.querySelectorAll(selectors.join(','));
    let idx = 0;
    for (const el of all) {
        try {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (rect.width < 3 || rect.height < 3) continue;
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            if (parseFloat(style.opacity) < 0.1) continue;
            if (rect.top < 0 || rect.left < 0) continue;
            if (rect.top > window.innerHeight || rect.left > window.innerWidth) continue;

            // نص العنصر
            let text = (el.innerText || el.textContent || '').trim().slice(0, 120);
            if (!text) text = el.getAttribute('aria-label') || '';
            if (!text) text = el.getAttribute('placeholder') || '';
            if (!text) text = el.getAttribute('title') || '';

            const key = `${el.tagName}|${text}|${Math.round(rect.top)}|${Math.round(rect.left)}`;
            if (seen.has(key)) continue;
            seen.add(key);

            results.push({
                id: idx++,
                tag: el.tagName.toLowerCase(),
                text: text,
                aria: el.getAttribute('aria-label') || '',
                name: el.getAttribute('name') || '',
                type: el.getAttribute('type') || '',
                disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
                checked: el.checked === true,
                x: Math.round(rect.left + rect.width / 2),
                y: Math.round(rect.top + rect.height / 2),
                w: Math.round(rect.width),
                h: Math.round(rect.height)
            });
        } catch (e) {}
    }
    return results.slice(0, 80);  // لا نرسل أكثر من 80 عنصر
}
"""


def _collect_elements(page) -> list[dict]:
    try:
        return page.evaluate(JS_COLLECT_INTERACTIVE)
    except Exception as e:
        _log(f"⚠️  فشل جمع العناصر: {e}")
        return []


def _screenshot_b64(page) -> str:
    png = page.screenshot(type="png", full_page=False)
    return base64.b64encode(png).decode()


# ═══════════════════════════════════════════════════════
#  استدعاء Gemini Vision
# ═══════════════════════════════════════════════════════

SYSTEM_PROMPT = """أنت مساعد أتمتة رفع الفيديوهات على YouTube Studio.

مهمتك: تنظر إلى لقطة شاشة + قائمة العناصر القابلة للتفاعل، وتقرر الخطوة التالية لرفع فيديو.

**المسار الكامل:**
1. إذا ظهرت شاشة "Details": اكتب العنوان والوصف
2. إذا ظهرت شاشة "Video elements": تخطَّ
3. إذا ظهرت شاشة "Checks": تخطَّ
4. إذا ظهرت شاشة "Made for Kids": اختر "ليس للأطفال" (NOT_MFK)
5. إذا ظهرت شاشة "Visibility": اختر "Public"
6. ثم اضغط "Next" حتى الشاشة الأخيرة
7. في الشاشة الأخيرة: اضغط "Publish"

**قواعد مهمة:**
- لا تختر "made for kids" أبداً
- لا تضغط "Save" أو "Close" بدون سبب
- إذا شفت زر "Next" مفعّل → اضغطه
- إذا شفت خيار "Public" غير محدد → حدده أولاً
- إذا شفت "Publish" مفعّل → اضغطه
- إذا الشاشة الحالية لا تحتاج فعل → ارجع {"action": "wait"}
- إذا انتهى الرفع بنجاح (رأيت رسالة تأكيد أو رابط الفيديو) → ارجع {"action": "done"}

**الإخراج:** JSON فقط، بدون شرح، بأحد هذه الأشكال:
- {"action": "click", "id": 5}
- {"action": "type", "id": 2, "text": "المحتوى"}
- {"action": "wait", "seconds": 3}
- {"action": "done"}
- {"action": "fail", "reason": "..."}
"""


def _gemini_decide(page, step: int, history: list[str]) -> dict:
    elements = _collect_elements(page)
    img_b64 = _screenshot_b64(page)

    # نضغط قائمة العناصر بشكل مختصر
    elements_txt = "\n".join([
        f"[{e['id']}] <{e['tag']}> \"{e['text'][:60]}\" "
        f"name={e['name']} type={e['type']} "
        f"disabled={e['disabled']} checked={e['checked']} "
        f"@({e['x']},{e['y']}) {e['w']}x{e['h']}"
        for e in elements
    ])

    recent_history = "\n".join(history[-6:]) if history else "(لا شيء بعد)"

    prompt = f"""**الخطوة الحالية: {step}**

**سجل آخر {len(history[-6:])} خطوات:**
{recent_history}

**العناصر القابلة للتفاعل في الشاشة الحالية ({len(elements)} عنصر):**
{elements_txt}

بناءً على الصورة + العناصر، ما هي الخطوة التالية؟ أجب بـ JSON فقط."""

    body = {
        "contents": [{
            "parts": [
                {"text": SYSTEM_PROMPT},
                {"text": prompt},
                {"inlineData": {"mimeType": "image/png", "data": img_b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 300,
            "responseMimeType": "application/json",
        },
    }

    p = pool()
    for attempt in range(len(p.keys) * 2):
        key = p.get()
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={key}",
                json=body,
                timeout=60,
            )
            if r.status_code == 429:
                p.fail(60)
                continue
            if r.status_code != 200:
                _log(f"⚠️  HTTP {r.status_code}: {r.text[:200]}")
                p.fail(30)
                continue

            data = r.json()
            cands = data.get("candidates") or []
            if not cands:
                p.fail(30)
                continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            txt = ""
            for part in parts:
                if "text" in part:
                    txt += part["text"]
            txt = txt.strip()
            _log(f"🤖 Gemini raw: {txt[:200]}")

            # استخرج JSON
            m = re.search(r'\{[\s\S]*\}', txt)
            if not m:
                p.fail(20)
                continue
            decision = json.loads(m.group(0))
            decision["_elements"] = elements  # نحفظ العناصر للتنفيذ
            return decision

        except Exception as e:
            _log(f"⚠️  {str(e)[:120]}")
            p.fail(20)

    raise RuntimeError("فشل Gemini في اتخاذ قرار")


# ═══════════════════════════════════════════════════════
#  تنفيذ قرار Gemini
# ═══════════════════════════════════════════════════════

def _execute(page, decision: dict, screenshot_dir: str, step: int) -> str:
    action = decision.get("action", "wait")
    elements = decision.get("_elements", [])

    def find_el(eid):
        for e in elements:
            if e["id"] == eid:
                return e
        return None

    if action == "done":
        _log("✅ Gemini أعلن الانتهاء")
        return "done"

    if action == "fail":
        reason = decision.get("reason", "?")
        _log(f"❌ Gemini أعلن الفشل: {reason}")
        return f"fail: {reason}"

    if action == "wait":
        sec = int(decision.get("seconds", 3))
        _log(f"⏸️  انتظار {sec}ث")
        page.wait_for_timeout(sec * 1000)
        return "wait"

    if action == "click":
        eid = decision.get("id")
        el = find_el(eid)
        if not el:
            _log(f"⚠️  عنصر {eid} غير موجود")
            return f"err: element {eid} not found"

        _log(f"👆 ضغط على [{eid}] '{el['text'][:50]}' @({el['x']},{el['y']})")

        # 3 طرق: JS click على الموقع، ثم mouse click إحداثيات، ثم locator
        clicked = False

        # 1) نحاول نحدد العنصر بـ JS ونضغطه
        try:
            js = f"""
            () => {{
                const all = document.querySelectorAll(
                    'button,a,input,textarea,select,[role="button"],[role="radio"],' +
                    '[role="checkbox"],[role="tab"],tp-yt-paper-radio-button,' +
                    'ytcp-button,ytcp-radio-button,tp-yt-paper-item,tp-yt-paper-button,' +
                    '[contenteditable="true"]'
                );
                const target = all[{eid}];
                if (target) {{ target.click(); return true; }}
                return false;
            }}
            """
            # ⚠️ ملاحظة: id من collection مش نفس index all — نستخدم الإحداثيات
            # نكتفي بالضغط بالإحداثيات هنا
        except Exception:
            pass

        # 2) ضغط بالإحداثيات — الأكثر موثوقية
        try:
            page.mouse.click(el["x"], el["y"])
            clicked = True
            _log(f"   ✅ mouse click @({el['x']},{el['y']})")
        except Exception as e:
            _log(f"   ⚠️  mouse: {e}")

        # 3) fallback: locator بـ name
        if not clicked and el.get("name"):
            try:
                page.locator(f'[name="{el["name"]}"]').first.click(force=True, timeout=5000)
                clicked = True
                _log(f"   ✅ locator name={el['name']}")
            except Exception as e:
                _log(f"   ⚠️  locator: {e}")

        page.wait_for_timeout(2500)
        page.screenshot(path=f"{screenshot_dir}/ai_step_{step}_after_click.png")
        return f"clicked '{el['text'][:40]}'"

    if action == "type":
        eid = decision.get("id")
        text = decision.get("text", "")
        el = find_el(eid)
        if not el:
            _log(f"⚠️  عنصر {eid} غير موجود")
            return f"err: element {eid} not found"

        _log(f"⌨️  كتابة في [{eid}]: '{text[:50]}'")
        try:
            page.mouse.click(el["x"], el["y"])
            page.wait_for_timeout(500)
            page.keyboard.press("Control+A")
            page.wait_for_timeout(200)
            page.keyboard.press("Delete")
            page.wait_for_timeout(200)
            page.keyboard.type(text, delay=15)
            page.wait_for_timeout(800)
            _log(f"   ✅ تم")
        except Exception as e:
            _log(f"   ❌ {e}")
            return f"err: {e}"
        return f"typed '{text[:30]}'"

    return "unknown"


# ═══════════════════════════════════════════════════════
#  الدالة الرئيسية
# ═══════════════════════════════════════════════════════

def _parse_cookies(raw: str) -> list[dict]:
    data = json.loads(raw)
    if isinstance(data, list):
        out = []
        for c in data:
            if not c.get("name") or c.get("value") is None:
                continue
            item = {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ".youtube.com"),
                "path": c.get("path", "/"),
            }
            if c.get("expirationDate"):
                item["expires"] = float(c["expirationDate"])
            if c.get("httpOnly") is not None:
                item["httpOnly"] = bool(c["httpOnly"])
            if c.get("secure") is not None:
                item["secure"] = bool(c["secure"])
            ss = c.get("sameSite")
            if ss in ("no_restriction", "None"):
                item["sameSite"] = "None"
            elif ss in ("lax", "Lax"):
                item["sameSite"] = "Lax"
            elif ss in ("strict", "Strict"):
                item["sameSite"] = "Strict"
            out.append(item)
        return out
    if isinstance(data, dict):
        return [
            {"name": k, "value": v, "domain": ".youtube.com", "path": "/"}
            for k, v in data.items()
        ]
    raise ValueError(f"صيغة غير معروفة")


def upload_video_ai(
    video_path: str,
    title: str,
    description: str,
    cookies_raw: str,
    headless: bool = True,
    screenshot_dir: str = "out/screenshots",
    max_steps: int = 40,
) -> bool:
    video_path = str(Path(video_path).resolve())
    if not Path(video_path).exists():
        raise FileNotFoundError(video_path)

    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    cookies_list = _parse_cookies(cookies_raw)
    _log(f"🍪 {len(cookies_list)} كوكي")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Asia/Damascus",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        context.add_cookies(cookies_list)
        page = context.new_page()

        history: list[str] = []

        try:
            _log("🌐 فتح صفحة الرفع...")
            page.goto(
                "https://www.youtube.com/upload",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(6000)

            if "signin" in page.url.lower() or "accounts.google" in page.url.lower():
                raise RuntimeError("❌ فشل تسجيل الدخول")
            _log("✅ مسجّل")

            # ═══ رفع الفيديو بأنفسنا (لأن Gemini ما يقدر) ═══
            _log(f"📁 رفع: {Path(video_path).name}")
            file_input = None
            for _ in range(6):
                c = page.locator('input[type="file"]')
                if c.count() > 0:
                    file_input = c.first
                    break
                page.wait_for_timeout(3000)
            if file_input is None:
                raise RuntimeError("❌ ما لقيت input")

            file_input.set_input_files(video_path)
            _log("⏳ انتظار 30 ثانية لبدء المعالجة...")
            page.wait_for_timeout(30000)

            # ═══ الحلقة الذكية ═══
            _log("═" * 50)
            _log("🧠 نبدأ حلقة Gemini Vision")
            _log(f"📝 العنوان: {title}")
            _log(f"📝 الوصف: {description[:80]}")

            # نحفظ المعلومات في history عشان Gemini يعرف
            history.append(f"العنوان المطلوب: {title}")
            history.append(f"الوصف المطلوب: {description[:200]}")

            done = False
            for step in range(1, max_steps + 1):
                _log(f"\n{'─' * 50}")
                _log(f"🎯 خطوة {step}")

                page.screenshot(path=f"{screenshot_dir}/ai_step_{step}_before.png")

                try:
                    decision = _gemini_decide(page, step, history)
                except Exception as e:
                    _log(f"❌ فشل Gemini: {e}")
                    break

                action = decision.get("action")
                _log(f"📋 القرار: {action} — {decision}")

                result = _execute(page, decision, screenshot_dir, step)
                history.append(f"خطوة {step}: {action} → {result}")

                if result == "done":
                    done = True
                    break
                if result.startswith("fail"):
                    _log(f"❌ Gemini أعلن فشل: {result}")
                    break
                if result.startswith("err"):
                    _log(f"⚠️  خطأ في التنفيذ: {result}")

                page.wait_for_timeout(2000)

            page.screenshot(path=f"{screenshot_dir}/ai_final.png")

            if done:
                _log("🎉 اكتمل الرفع عبر Gemini")
                return True

            _log("⚠️  الحلقة انتهت بدون تأكيد")
            return False

        except Exception as e:
            try:
                page.screenshot(path=f"{screenshot_dir}/ai_error.png")
            except Exception:
                pass
            _log(f"❌ {e}")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    cookies_raw = os.getenv("YOUTUBE_COOKIES", "[]")
    ok = upload_video_ai(
        video_path="out/video.mp4",
        title=os.getenv("YT_TITLE", "اختبار رفع آلي — مالك فاكتوري"),
        description=os.getenv("YT_DESC", "رفع تجريبي من مصنع مالك."),
        cookies_raw=cookies_raw,
        headless=True,
    )
    exit(0 if ok else 1)
