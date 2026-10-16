"""رفع فيديو على يوتيوب عبر Playwright — نسخة تعالج Modal الأطفال أولاً."""
from __future__ import annotations

import json
import os
import re
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


def _wait_no_overlay(page, max_wait: int = 20) -> bool:
    """ينتظر اختفاء أي overlay (backdrop) من الشاشة."""
    for i in range(max_wait):
        try:
            overlays = page.locator('tp-yt-iron-overlay-backdrop.opened')
            if overlays.count() == 0:
                return True
        except Exception:
            return True
        page.wait_for_timeout(1000)
    return False


def _handle_kids_modal(page, screenshot_dir: str) -> bool:
    """
    يعالج Modal "Made for Kids" الذي يظهر بعد رفع الفيديو.
    يرجع True إذا عالجه، False إذا ما كان موجود.
    """
    _log("🔎 فحص Modal الأطفال...")

    # انتظر ظهور modal (يظهر خلال 3-10 ثواني بعد الرفع)
    modal_appeared = False
    for i in range(10):
        try:
            modal = page.locator('tp-yt-paper-dialog[opened], ytcp-modal[opened]')
            if modal.count() > 0:
                # تأكد إنه modal الأطفال تحديداً
                text = modal.first.inner_text(timeout=2000)
                if re.search(r"kids|الأطفال|طفل|Made for", text, re.I):
                    modal_appeared = True
                    _log(f"✅ Modal الأطفال ظهر (طول النص: {len(text)})")
                    break
        except Exception:
            pass
        page.wait_for_timeout(1000)

    if not modal_appeared:
        _log("ℹ️  لا يوجد Modal للأطفال — أتابع")
        return False

    page.screenshot(path=f"{screenshot_dir}/kids_modal_appeared.png")

    # اختر "ليس للأطفال"
    _log("👶 اختيار: ليس للأطفال...")
    not_kids_selectors = [
        'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
        'ytcp-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
        'tp-yt-paper-radio-button:has-text("Not made for kids")',
        'tp-yt-paper-radio-button:has-text("ليس للأطفال")',
    ]
    kids_clicked = False
    for sel in not_kids_selectors:
        try:
            page.locator(sel).first.click(force=True, timeout=8000)
            _log(f"✅ اخترنا: {sel}")
            kids_clicked = True
            break
        except Exception:
            continue

    if not kids_clicked:
        _log("⚠️  ما قدرنا نختار — نحاول نجبر الحفظ مباشرة")

    page.wait_for_timeout(1500)

    # اضغط Save
    _log("💾 حفظ الـ Modal...")
    save_clicked = False
    for sel in [
        'ytcp-button#save-button',
        'ytcp-button:has-text("Save")',
        'ytcp-button:has-text("حفظ")',
        'tp-yt-paper-button:has-text("Save")',
    ]:
        try:
            page.locator(sel).first.click(force=True, timeout=8000)
            _log(f"✅ حفظنا: {sel}")
            save_clicked = True
            break
        except Exception:
            continue

    page.wait_for_timeout(3000)
    page.screenshot(path=f"{screenshot_dir}/after_kids_modal.png")

    # انتظر يختفي overlay
    _wait_no_overlay(page, max_wait=15)
    _log("✅ انتهينا من Modal الأطفال")

    return True


def _set_text_field(page, locator, text: str, label: str) -> None:
    """يكتب نصاً في حقل contenteditable بأمان."""
    try:
        locator.wait_for(state="visible", timeout=15000)
    except PWTimeout:
        _log(f"⚠️  {label} ما ظهر")
        raise

    # حاول نضغط force
    try:
        locator.click(force=True, timeout=10000)
    except Exception:
        _log(f"⚠️  ضغط عادي فشل على {label} — نتابع")

    page.wait_for_timeout(500)
    # محدّد الكل + مسح
    page.keyboard.press("Control+A")
    page.wait_for_timeout(200)
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)

    # اكتب النص بحروف
    try:
        page.keyboard.type(text, delay=15)
    except Exception:
        # بديل: استخدم fill
        try:
            locator.fill(text)
        except Exception as e:
            _log(f"⚠️  فشل كتابة {label}: {e}")
            raise

    page.wait_for_timeout(800)
    _log(f"✅ كتبنا {label}: {text[:60]}...")


def upload_video(
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
    required = {"SID", "LOGIN_INFO"}
    missing = required - names
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
            # ═══════ 1) فتح صفحة الرفع ═══════
            _log("🌐 فتح صفحة الرفع مباشرة...")
            page.goto(
                "https://www.youtube.com/upload",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(6000)
            page.screenshot(path=f"{screenshot_dir}/01_goto_upload.png")
            _log(f"📍 الرابط: {page.url}")

            if "signin" in page.url.lower() or "accounts.google" in page.url.lower():
                page.screenshot(path=f"{screenshot_dir}/login_failed.png")
                raise RuntimeError("❌ فشل تسجيل الدخول")

            _log("✅ مسجّل الدخول")

            # ═══════ 2) انتظر input الفيديو ═══════
            file_input = None
            for attempt in range(6):
                c = page.locator('input[type="file"]')
                if c.count() > 0:
                    file_input = c.first
                    _log(f"✅ لقينا input في المحاولة {attempt+1}")
                    break
                page.wait_for_timeout(3000)

            if file_input is None:
                page.screenshot(path=f"{screenshot_dir}/02_no_input.png")
                raise RuntimeError("❌ ما لقيت input الفيديو")

            # ═══════ 3) ارفع الفيديو ═══════
            _log(f"📁 رفع: {Path(video_path).name}")
            file_input.set_input_files(video_path)
            _log("⏳ انتظار معالجة الرفع الأولي...")
            page.wait_for_timeout(8000)
            page.screenshot(path=f"{screenshot_dir}/03_after_upload.png")

            # ═══════ 4) 🎯 نعالج Modal الأطفال أولاً! ═══════
            _handle_kids_modal(page, screenshot_dir)

            # ═══════ 5) الآن اكتب العنوان ═══════
            _log("⏳ انتظار حقول التفاصيل...")
            page.wait_for_timeout(3000)

            title_box = page.locator(
                'ytcp-social-suggestions-textbox #textbox, '
                '#textbox[contenteditable="true"]'
            ).first
            _set_text_field(page, title_box, title[:95], "العنوان")
            page.screenshot(path=f"{screenshot_dir}/04_title_done.png")

            # ═══════ 6) اكتب الوصف ═══════
            try:
                all_boxes = page.locator('#textbox[contenteditable="true"]')
                if all_boxes.count() > 1:
                    desc_box = all_boxes.nth(1)
                    _set_text_field(page, desc_box, description[:4900], "الوصف")
            except Exception as e:
                _log(f"⚠️  فشل الوصف: {e}")

            page.screenshot(path=f"{screenshot_dir}/05_desc_done.png")

            # ═══════ 7) Next × 3 ═══════
            _log("➡️  التخطي (3 مرات)...")
            for i in range(3):
                ok = _try_click(page, [
                    "ytcp-button#next-button",
                    'ytcp-button:has-text("Next")',
                ], timeout=20000, force=True)
                if not ok:
                    _log(f"⚠️  ما لقيت Next {i+1}")
                    break
                page.wait_for_timeout(3000)

            page.screenshot(path=f"{screenshot_dir}/06_after_next.png")

            # ═══════ 8) Public ═══════
            _log("🌍 Public...")
            try:
                page.locator(
                    'tp-yt-paper-radio-button[name="PUBLIC"], '
                    'ytcp-radio-button[name="PUBLIC"]'
                ).first.click(force=True, timeout=8000)
                page.wait_for_timeout(1500)
                _log("✅ Public محدد")
            except Exception:
                _log("⚠️  Public مش متاح")

            page.screenshot(path=f"{screenshot_dir}/07_before_publish.png")

            # ═══════ 9) Publish ═══════
            _log("🚀 النشر...")
            published = _try_click(page, [
                "ytcp-button#done-button",
                'ytcp-button:has-text("Publish")',
                'ytcp-button:has-text("Save")',
            ], timeout=30000, force=True)

            if not published:
                page.screenshot(path=f"{screenshot_dir}/08_no_publish.png")
                raise RuntimeError("❌ فشل النشر")

            page.wait_for_timeout(10000)
            page.screenshot(path=f"{screenshot_dir}/09_done.png")
            _log("🎉 تم النشر بنجاح")
            return True

        except Exception as e:
            try:
                page.screenshot(path=f"{screenshot_dir}/error.png")
                _log(f"📸 screenshot: {screenshot_dir}/error.png")
            except Exception:
                pass
            _log(f"❌ خطأ: {e}")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    cookies_raw = os.getenv("YOUTUBE_COOKIES", "[]")
    upload_video(
        video_path="out/video.mp4",
        title=os.getenv("YT_TITLE", "اختبار رفع آلي — مالك فاكتوري"),
        description=os.getenv("YT_DESC", "رفع تجريبي من مصنع مالك."),
        cookies_raw=cookies_raw,
        headless=True,
    )
