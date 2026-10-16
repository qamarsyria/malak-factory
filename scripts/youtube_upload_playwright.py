"""رفع فيديو على يوتيوب عبر Playwright (بدون API رسمي)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def _log(msg: str) -> None:
    print(f"[yt-bot] {msg}", flush=True)


def _try_click(page, selectors: list[str], timeout: int = 15000) -> bool:
    """يحاول عدة selectors بالترتيب حتى ينجح أحدها."""
    for sel in selectors:
        try:
            page.click(sel, timeout=timeout)
            return True
        except PWTimeout:
            continue
    return False


def upload_video(
    video_path: str,
    title: str,
    description: str,
    cookies_json: dict,
    headless: bool = True,
    screenshot_dir: str = "out/screenshots",
) -> bool:
    video_path = str(Path(video_path).resolve())
    if not Path(video_path).exists():
        raise FileNotFoundError(f"الفيديو غير موجود: {video_path}")

    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="ar-SA",
        )

        # حقن الـ cookies
        cookies_list = [
            {
                "name": name,
                "value": value,
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "None",
            }
            for name, value in cookies_json.items()
        ]
        context.add_cookies(cookies_list)
        _log(f"🍪 حُقن {len(cookies_list)} كوكي")

        page = context.new_page()

        try:
            # 1) فتح YouTube Studio
            _log("🌐 فتح YouTube Studio...")
            page.goto(
                "https://studio.youtube.com",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(4000)

            if "signin" in page.url.lower() or "accounts.google" in page.url.lower():
                page.screenshot(path=f"{screenshot_dir}/login_failed.png")
                raise RuntimeError("❌ فشل تسجيل الدخول — الـ cookies انتهت")

            _log("✅ مسجّل الدخول")

            # 2) زر الرفع
            _log("🔘 فتح نافذة الرفع...")
            if not _try_click(page, [
                "ytcp-button#create-icon",
                "#create-icon",
                'button:has-text("Create")',
                'button:has-text("إنشاء")',
            ], timeout=30000):
                page.screenshot(path=f"{screenshot_dir}/no_create_btn.png")
                raise RuntimeError("لم أجد زر الإنشاء")

            page.wait_for_timeout(1500)

            if not _try_click(page, [
                'tp-yt-paper-item:has-text("Upload video")',
                'tp-yt-paper-item:has-text("رفع فيديو")',
                'tp-yt-paper-item:has-text("رفع")',
            ], timeout=15000):
                page.screenshot(path=f"{screenshot_dir}/no_upload_item.png")
                raise RuntimeError("لم أجد خيار رفع الفيديو")

            page.wait_for_timeout(2000)

            # 3) رفع الملف
            _log(f"📁 رفع: {Path(video_path).name}")
            file_input = page.locator('input[type="file"]').first
            file_input.set_input_files(video_path)
            page.wait_for_timeout(4000)

            # 4) العنوان
            _log(f"📝 العنوان: {title[:80]}")
            title_box = page.locator(
                '#textbox[contenteditable="true"], '
                'ytcp-social-suggestions-textbox #textbox'
            ).first
            title_box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            title_box.fill(title[:95])
            page.wait_for_timeout(1000)

            # 5) الوصف
            desc_box = page.locator('#textbox[contenteditable="true"]').nth(1)
            try:
                desc_box.click(timeout=5000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                desc_box.fill(description[:4900])
                page.wait_for_timeout(1000)
            except PWTimeout:
                _log("⚠️  حقل الوصف غير متاح — أتجاهل")

            # 6) Made for Kids modal (YouTube 2024+)
            _log("👶 اختيار: ليس للأطفال")
            page.wait_for_timeout(2000)
            kids_radio = page.locator(
                'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"], '
                'ytcp-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'
            ).first
            try:
                kids_radio.click(timeout=10000)
                page.wait_for_timeout(1000)
                _try_click(page, [
                    'ytcp-button#save-button',
                    'ytcp-button:has-text("Save")',
                    'ytcp-button:has-text("حفظ")',
                ], timeout=5000)
            except PWTimeout:
                _log("⚠️  لا يوجد modal للأطفال — أتابع")

            # 7) Next × 3
            _log("➡️  تخطي الشاشات...")
            for i in range(3):
                if not _try_click(page, [
                    "ytcp-button#next-button",
                    'ytcp-button:has-text("Next")',
                    'ytcp-button:has-text("التالي")',
                ], timeout=20000):
                    page.screenshot(path=f"{screenshot_dir}/no_next_{i}.png")
                    break
                page.wait_for_timeout(2500)

            # 8) Visibility = Public
            _log("🌍 جعل الفيديو عاماً...")
            try:
                page.click(
                    'tp-yt-paper-radio-button[name="PUBLIC"], '
                    'ytcp-radio-button[name="PUBLIC"]',
                    timeout=8000,
                )
                page.wait_for_timeout(1000)
            except PWTimeout:
                _log("⚠️  لم أستطع اختيار Public — الافتراضي محفوظ")

            # 9) Publish
            _log("🚀 النشر...")
            if not _try_click(page, [
                "ytcp-button#done-button",
                'ytcp-button:has-text("Publish")',
                'ytcp-button:has-text("نشر")',
                'ytcp-button:has-text("Save")',
            ], timeout=30000):
                page.screenshot(path=f"{screenshot_dir}/no_publish.png")
                raise RuntimeError("فشل النشر")

            page.wait_for_timeout(5000)
            _log("🎉 تم النشر")
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
    cookies_raw = os.getenv("YOUTUBE_COOKIES", "{}")
    cookies = json.loads(cookies_raw)

    upload_video(
        video_path="out/video.mp4",
        title=os.getenv("YT_TITLE", "اختبار رفع آلي — مالك فاكتوري"),
        description=os.getenv("YT_DESC", "رفع تجريبي من مصنع مالك."),
        cookies_json=cookies,
        headless=True,
    )
