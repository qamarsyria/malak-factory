"""رفع فيديو على يوتيوب عبر Playwright — نسخة محدّثة تتعامل مع القنوات الجديدة."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def _log(msg: str) -> None:
    print(f"[yt-bot] {msg}", flush=True)


def _try_click(page, selectors: list[str], timeout: int = 15000) -> bool:
    for sel in selectors:
        try:
            page.click(sel, timeout=timeout)
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
            # ─────── 1) اذهب مباشرة لصفحة الرفع ───────
            _log("🌐 فتح صفحة الرفع مباشرة...")
            page.goto(
                "https://www.youtube.com/upload",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(6000)

            # صوّر الصفحة للمساعدة في التشخيص
            page.screenshot(path=f"{screenshot_dir}/01_after_goto_upload.png")

            current_url = page.url
            _log(f"📍 الرابط الحالي: {current_url}")

            # ─────── 2) التحقق من تسجيل الدخول ───────
            if "signin" in current_url.lower() or "accounts.google" in current_url.lower():
                page.screenshot(path=f"{screenshot_dir}/login_failed.png")
                raise RuntimeError("❌ فشل تسجيل الدخول — الـ cookies غير صالحة")

            _log("✅ مسجّل الدخول، جاهزين للرفع")

            # ─────── 3) انتظر input الفيديو ───────
            _log("⏳ انتظار input الرفع...")
            file_input = None
            for attempt in range(6):
                candidates = page.locator('input[type="file"]')
                count = candidates.count()
                _log(f"🔍 محاولة {attempt+1}: وجدت {count} input file")
                if count > 0:
                    file_input = candidates.first
                    break
                # أحياناً نحتاج نضغط زر أو ننتظر أكثر
                page.wait_for_timeout(3000)

            if file_input is None:
                page.screenshot(path=f"{screenshot_dir}/02_no_file_input.png")
                # نحاول نطبع HTML للمساعدة
                html_snippet = page.content()[:3000]
                _log(f"HTML snippet: {html_snippet[:500]}")
                raise RuntimeError("❌ ما لقيت input الفيديو")

            # ─────── 4) ارفع الفيديو ───────
            _log(f"📁 رفع: {Path(video_path).name}")
            file_input.set_input_files(video_path)
            page.wait_for_timeout(8000)
            page.screenshot(path=f"{screenshot_dir}/03_after_upload.png")

            # ─────── 5) انتظر نموذج التفاصيل ───────
            _log("⏳ انتظار نموذج التفاصيل...")
            details_ready = False
            for i in range(15):
                title_box = page.locator(
                    '#textbox[contenteditable="true"], '
                    'ytcp-social-suggestions-textbox #textbox, '
                    'div[aria-label*="title" i][contenteditable="true"]'
                )
                if title_box.count() > 0:
                    details_ready = True
                    break
                page.wait_for_timeout(2000)

            if not details_ready:
                page.screenshot(path=f"{screenshot_dir}/04_no_details.png")
                raise RuntimeError("❌ نموذج التفاصيل ما ظهر")

            _log("✅ نموذج التفاصيل جاهز")

            # ─────── 6) العنوان ───────
            _log(f"📝 العنوان: {title[:80]}")
            title_box = page.locator(
                '#textbox[contenteditable="true"], '
                'ytcp-social-suggestions-textbox #textbox'
            ).first
            title_box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(500)
            title_box.fill(title[:95])
            page.wait_for_timeout(1500)

            # ─────── 7) الوصف ───────
            desc_box = page.locator('#textbox[contenteditable="true"]').nth(1)
            try:
                desc_box.click(timeout=5000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                desc_box.fill(description[:4900])
                page.wait_for_timeout(1000)
            except PWTimeout:
                _log("⚠️  حقل الوصف غير متاح")

            page.screenshot(path=f"{screenshot_dir}/05_title_desc.png")

            # ─────── 8) ليس للأطفال ───────
            _log("👶 اختيار: ليس للأطفال")
            page.wait_for_timeout(2000)
            try:
                page.locator(
                    'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"], '
                    'ytcp-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'
                ).first.click(timeout=10000)
                page.wait_for_timeout(1500)
            except PWTimeout:
                _log("⚠️  ما لقيت خيار الأطفال — أتابع")

            # ─────── 9) Next × 3 ───────
            _log("➡️  تخطي الشاشات (3 مرات)...")
            for i in range(3):
                if not _try_click(page, [
                    "ytcp-button#next-button",
                    'ytcp-button:has-text("Next")',
                ], timeout=20000):
                    page.screenshot(path=f"{screenshot_dir}/06_no_next_{i}.png")
                    _log(f"⚠️  ما لقيت Next رقم {i+1} — أتابع على أي حال")
                    break
                page.wait_for_timeout(3000)

            page.screenshot(path=f"{screenshot_dir}/07_before_publish.png")

            # ─────── 10) Public ───────
            _log("🌍 اختيار Public...")
            try:
                page.click(
                    'tp-yt-paper-radio-button[name="PUBLIC"], '
                    'ytcp-radio-button[name="PUBLIC"]',
                    timeout=8000,
                )
                page.wait_for_timeout(1500)
            except PWTimeout:
                _log("⚠️  Public مش متاح — الافتراضي محفوظ")

            # ─────── 11) Publish ───────
            _log("🚀 الضغط على زر النشر...")
            if not _try_click(page, [
                "ytcp-button#done-button",
                'ytcp-button:has-text("Publish")',
                'ytcp-button:has-text("Save")',
            ], timeout=30000):
                page.screenshot(path=f"{screenshot_dir}/08_no_publish.png")
                raise RuntimeError("❌ فشل النشر")

            page.wait_for_timeout(8000)
            page.screenshot(path=f"{screenshot_dir}/09_done.png")
            _log("🎉 تم النشر بنجاح")
            return True

        except Exception as e:
            try:
                page.screenshot(path=f"{screenshot_dir}/error.png")
                _log(f"📸 screenshot: {screenshot_dir}/error.png")
            except Exception:
                pass
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
