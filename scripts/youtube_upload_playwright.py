"""رفع فيديو على يوتيوب — نسخة تستخدم JS click + تحقق فعلي من الحالة."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def _log(msg: str) -> None:
    print(f"[yt-bot] {msg}", flush=True)


def _try_click(page, selectors: list[str], timeout: int = 15000, force: bool = False) -> bool:
    for sel in selectors:
        try:
            page.click(sel, timeout=timeout, force=force)
            _log(f"✅ نجح: {sel}")
            return True
        except PWTimeout:
            continue
        except Exception as e:
            _log(f"⚠️  {sel} → {str(e)[:80]}")
            continue
    return False


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
    raise ValueError(f"صيغة كوكيز غير معروفة: {type(data)}")


def _is_radio_checked(radio) -> bool:
    """يتحقق فعلياً إذا الـ radio محدد — يقرأ الـ property وليس الـ attribute."""
    try:
        return bool(radio.evaluate("el => el.checked === true || el.hasAttribute('checked')"))
    except Exception:
        return False


def _select_radio_robust(page, name: str, screenshot_dir: str, label: str) -> bool:
    """
    يحدد radio button بشكل موثوق.
    يستخدم JS click لأنه ضروري لعناصر Polymer.
    """
    _log(f"🎯 محاولة تحديد: {label} ({name})")

    selector = f'tp-yt-paper-radio-button[name="{name}"]'
    radio = page.locator(selector).first

    if radio.count() == 0:
        _log(f"⚠️  {name} غير موجود في DOM")
        return False

    # إذا محدد مسبقاً
    if _is_radio_checked(radio):
        _log(f"ℹ️  {name} محدد مسبقاً")
        return True

    # ═══ 5 طرق للضغط — بالترتيب ═══
    attempts = [
        ("JS click على العنصر نفسه",
         lambda: radio.evaluate("el => el.click()")),
        ("JS click على radioContainer الداخلي",
         lambda: page.locator(f'{selector} #radioContainer').first.evaluate("el => el.click()")),
        ("ضغط على الـ label الداخلي",
         lambda: page.locator(f'{selector} #radioLabel').first.click(force=True, timeout=5000)),
        ("ضغط عادي force",
         lambda: radio.click(force=True, timeout=5000)),
        ("ضغط بـ dispatchEvent كامل",
         lambda: radio.evaluate("""
            el => {
                const ev = new MouseEvent('click', {bubbles: true, cancelable: true, view: window});
                el.dispatchEvent(ev);
                if (el.click) el.click();
            }
         """)),
    ]

    for attempt_name, attempt_fn in attempts:
        try:
            _log(f"   🔸 {attempt_name}...")
            attempt_fn()
            page.wait_for_timeout(1800)

            # تحقق فعلي
            if _is_radio_checked(radio):
                _log(f"   ✅ نجح! ({attempt_name})")
                page.screenshot(path=f"{screenshot_dir}/radio_{name}_selected.png")
                return True
            else:
                _log(f"   ❌ لم يُسجَّل (حاول اللي بعده)")
        except Exception as e:
            _log(f"   ❌ فشل: {str(e)[:80]}")

    _log(f"❌ {name} — كل الطرق فشلت")
    page.screenshot(path=f"{screenshot_dir}/radio_{name}_FAILED.png")
    return False


def _dump_radios(page, label: str) -> None:
    _log(f"🔍 [{label}] فحص radio buttons...")
    try:
        radios = page.locator('tp-yt-paper-radio-button, ytcp-radio-button')
        count = radios.count()
        _log(f"   وجدت {count} radio")
        for i in range(min(count, 30)):
            try:
                r = radios.nth(i)
                name = r.get_attribute("name") or "?"
                visible = r.is_visible()
                checked_prop = r.evaluate("el => el.checked === true") if visible else False
                checked_attr = r.get_attribute("checked")
                if visible:
                    _log(f"   [{i}] name={name} checked_prop={checked_prop} checked_attr={checked_attr}")
            except Exception:
                pass
    except Exception as e:
        _log(f"   فشل الفحص: {e}")


def _set_text_field(page, locator, text: str, label: str) -> None:
    try:
        locator.wait_for(state="visible", timeout=15000)
    except PWTimeout:
        _log(f"⚠️  {label} ما ظهر")
        raise
    try:
        locator.click(force=True, timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(500)
    page.keyboard.press("Control+A")
    page.wait_for_timeout(200)
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)
    page.keyboard.type(text, delay=15)
    page.wait_for_timeout(800)
    _log(f"✅ كتبنا {label}")


def _click_next(page) -> bool:
    return _try_click(page, [
        "ytcp-button#next-button",
        'ytcp-button:has-text("Next")',
    ], timeout=30000, force=True)


def _upload_video(
    video_path: str,
    title: str,
    description: str,
    cookies_raw: str,
    headless: bool = True,
    screenshot_dir: str = "out/screenshots",
) -> bool:
    video_path = str(Path(video_path).resolve())
    if not Path(video_path).exists():
        raise FileNotFoundError(f"الفيديو غير موجود: {video_path}")

    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    cookies_list = _parse_cookies(cookies_raw)
    _log(f"🍪 تم تحليل {len(cookies_list)} كوكي")

    names = {c["name"] for c in cookies_list}
    missing = {"SID", "LOGIN_INFO"} - names
    if missing:
        raise RuntimeError(f"❌ كوكيز أساسية ناقصة: {missing}")

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
        _log(f"✅ حُقن {len(cookies_list)} كوكي")

        page = context.new_page()

        try:
            # ═══ 1) فتح صفحة الرفع ═══
            _log("🌐 فتح صفحة الرفع...")
            page.goto(
                "https://www.youtube.com/upload",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(6000)
            page.screenshot(path=f"{screenshot_dir}/01_goto.png")

            if "signin" in page.url.lower() or "accounts.google" in page.url.lower():
                raise RuntimeError("❌ فشل تسجيل الدخول")

            _log("✅ مسجّل الدخول")

            # ═══ 2) input الفيديو ═══
            file_input = None
            for attempt in range(6):
                c = page.locator('input[type="file"]')
                if c.count() > 0:
                    file_input = c.first
                    break
                page.wait_for_timeout(3000)
            if file_input is None:
                raise RuntimeError("❌ ما لقيت input الفيديو")

            # ═══ 3) ارفع ═══
            _log(f"📁 رفع: {Path(video_path).name}")
            file_input.set_input_files(video_path)
            _log("⏳ انتظار 30 ثانية...")
            page.wait_for_timeout(30000)
            page.screenshot(path=f"{screenshot_dir}/02_uploaded.png")

            # ═══ 4) العنوان والوصف ═══
            _log("📝 العنوان...")
            title_box = page.locator(
                'ytcp-social-suggestions-textbox #textbox, '
                '#textbox[contenteditable="true"]'
            ).first
            _set_text_field(page, title_box, title[:95], "العنوان")

            try:
                all_boxes = page.locator('#textbox[contenteditable="true"]')
                if all_boxes.count() > 1:
                    _set_text_field(page, all_boxes.nth(1), description[:4900], "الوصف")
            except Exception as e:
                _log(f"⚠️  الوصف: {e}")

            page.screenshot(path=f"{screenshot_dir}/03_details.png")

            # ═══ 5) Next loop ═══
            _log("═" * 40)
            _log("🔁 نضغط Next في حلقة حتى نختار Public + Made for Kids")

            public_done = False
            kids_done = False
            max_steps = 8

            for step in range(max_steps):
                page.wait_for_timeout(2500)
                _dump_radios(page, f"خطوة {step+1}")
                page.screenshot(path=f"{screenshot_dir}/step_{step+1}.png")

                # Made for Kids
                kids_radio = page.locator(
                    'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'
                ).first
                if kids_radio.count() > 0 and kids_radio.is_visible():
                    if not _is_radio_checked(kids_radio):
                        _log(f"👶 [خطوة {step+1}] اختر 'ليس للأطفال'")
                        _select_radio_robust(
                            page,
                            "VIDEO_MADE_FOR_KIDS_NOT_MFK",
                            screenshot_dir,
                            "ليس للأطفال",
                        )
                    if _is_radio_checked(kids_radio):
                        kids_done = True
                        _log(f"   ✔️ Kids done")

                # Public
                public_radio = page.locator(
                    'tp-yt-paper-radio-button[name="PUBLIC"]'
                ).first
                if public_radio.count() > 0 and public_radio.is_visible():
                    if not _is_radio_checked(public_radio):
                        _log(f"🌍 [خطوة {step+1}] اختر Public")
                        _select_radio_robust(page, "PUBLIC", screenshot_dir, "Public")
                    if _is_radio_checked(public_radio):
                        public_done = True
                        _log(f"   ✔️ Public done")

                if public_done and kids_done:
                    _log("✅ اخترنا Public + Kids — جاهزين للنشر")
                    break

                # Next
                _log(f"➡️  [خطوة {step+1}] Next...")
                if not _click_next(page):
                    _log(f"⚠️  ما لقينا Next في الخطوة {step+1}")
                    break
                page.wait_for_timeout(3500)

            page.screenshot(path=f"{screenshot_dir}/08_after_loop.png")

            # ═══ 6) انتظر الزر يتفعّل ═══
            _log("⏳ انتظار تفعيل Publish...")
            for i in range(30):
                try:
                    btn = page.locator('ytcp-button#done-button')
                    if btn.count() > 0:
                        disabled = btn.first.get_attribute("aria-disabled")
                        hidden = btn.first.get_attribute("hidden")
                        if disabled != "true" and hidden is None:
                            _log(f"✅ الزر مفعّل بعد {i}ث")
                            break
                except Exception:
                    pass
                page.wait_for_timeout(1000)

            page.screenshot(path=f"{screenshot_dir}/09_before_publish.png")

            # ═══ 7) Publish ═══
            _log("🚀 Publish...")
            published = False
            try:
                page.locator('ytcp-button#done-button').first.evaluate("el => el.click()")
                published = True
                _log("✅ نجح: JS click على done-button")
            except Exception as e:
                _log(f"⚠️  JS click فشل: {e}")
                published = _try_click(page, [
                    "ytcp-button#done-button",
                    'ytcp-button:has-text("Publish")',
                ], timeout=30000, force=True)

            if not published:
                page.screenshot(path=f"{screenshot_dir}/10_no_publish.png")
                raise RuntimeError("❌ فشل النشر")

            _log("⏳ انتظار تأكيد النشر...")
            page.wait_for_timeout(15000)
            page.screenshot(path=f"{screenshot_dir}/11_done.png")
            _log("🎉 تم النشر بنجاح")
            return True

        except Exception as e:
            try:
                page.screenshot(path=f"{screenshot_dir}/error.png")
            except Exception:
                pass
            _log(f"❌ خطأ: {e}")
            raise
        finally:
            browser.close()


upload_video = _upload_video


if __name__ == "__main__":
    cookies_raw = os.getenv("YOUTUBE_COOKIES", "[]")
    upload_video(
        video_path="out/video.mp4",
        title=os.getenv("YT_TITLE", "اختبار رفع آلي — مالك فاكتوري"),
        description=os.getenv("YT_DESC", "رفع تجريبي من مصنع مالك."),
        cookies_raw=cookies_raw,
        headless=True,
    )
