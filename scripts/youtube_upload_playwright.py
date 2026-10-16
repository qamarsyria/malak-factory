"""رفع فيديو على يوتيوب — نسخة تعالج شاشة Visibility + Made for Kids بشكل صحيح."""
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


def _dump_radios(page, label: str) -> None:
    """يطبع كل radio buttons الموجودة لمساعدتنا في التشخيص."""
    _log(f"🔍 [{label}] فحص radio buttons...")
    try:
        radios = page.locator('tp-yt-paper-radio-button, ytcp-radio-button')
        count = radios.count()
        _log(f"   وجدت {count} radio")
        for i in range(min(count, 25)):
            try:
                name = radios.nth(i).get_attribute("name") or "?"
                visible = radios.nth(i).is_visible()
                checked = radios.nth(i).get_attribute("checked") or "false"
                _log(f"   [{i}] name={name} visible={visible} checked={checked}")
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


def _select_made_for_kids_no(page) -> bool:
    """يختار 'ليس للأطفال' في شاشة Visibility."""
    _log("👶 اختيار: ليس للأطفال...")
    selectors = [
        'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
        'ytcp-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
        '#radioLabel:has-text("No, it\'s not made for kids")',
        'tp-yt-paper-radio-button:has-text("ليس للأطفال")',
        'tp-yt-paper-radio-button:has-text("ليس موجّه")',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                el.click(force=True, timeout=8000)
                page.wait_for_timeout(1500)
                _log(f"✅ Made for Kids = No ({sel})")
                return True
        except Exception:
            continue
    _log("⚠️  ما لقيت خيار Made for Kids")
    return False


def _select_public(page) -> bool:
    """يختار Public في شاشة Visibility."""
    _log("🌍 اختيار Public...")
    selectors = [
        'tp-yt-paper-radio-button[name="PUBLIC"]',
        'ytcp-radio-button[name="PUBLIC"]',
        '#radioLabel:has-text("Public")',
        'tp-yt-paper-radio-button:has-text("عام")',
        'tp-yt-paper-radio-button:has-text("Public")',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                el.click(force=True, timeout=8000)
                page.wait_for_timeout(1500)
                _log(f"✅ Public ({sel})")
                return True
        except Exception:
            continue
    _log("⚠️  ما لقيت Public")
    return False


def _wait_done_button_enabled(page, max_wait: int = 30) -> bool:
    """ينتظر حتى يصير زر Publish/Done مفعّلاً."""
    _log("⏳ انتظار تفعيل زر Publish...")
    for i in range(max_wait):
        try:
            btn = page.locator('ytcp-button#done-button')
            if btn.count() > 0:
                is_disabled = btn.first.get_attribute("aria-disabled")
                is_hidden = btn.first.get_attribute("hidden")
                if is_disabled != "true" and is_hidden is None:
                    _log(f"✅ الزر مفعّل (بعد {i}ث)")
                    return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    _log(f"⚠️  الزر ما تفعّل بعد {max_wait}ث")
    return False


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
            # 1) فتح صفحة الرفع
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

            # 2) input الفيديو
            file_input = None
            for attempt in range(6):
                c = page.locator('input[type="file"]')
                if c.count() > 0:
                    file_input = c.first
                    break
                page.wait_for_timeout(3000)
            if file_input is None:
                raise RuntimeError("❌ ما لقيت input الفيديو")

            # 3) ارفع
            _log(f"📁 رفع: {Path(video_path).name}")
            file_input.set_input_files(video_path)
            _log("⏳ انتظار معالجة الرفع (30 ثانية)...")
            page.wait_for_timeout(30000)
            page.screenshot(path=f"{screenshot_dir}/02_uploaded.png")

            # 4) العنوان والوصف
            _log("📝 كتابة العنوان...")
            title_box = page.locator(
                'ytcp-social-suggestions-textbox #textbox, '
                '#textbox[contenteditable="true"]'
            ).first
            _set_text_field(page, title_box, title[:95], "العنوان")

            try:
                all_boxes = page.locator('#textbox[contenteditable="true"]')
                if all_boxes.count() > 1:
                    _set_text_field(
                        page, all_boxes.nth(1), description[:4900], "الوصف"
                    )
            except Exception as e:
                _log(f"⚠️  الوصف: {e}")

            page.screenshot(path=f"{screenshot_dir}/03_details.png")

            # 5) Next × 3 (بفواصل أطول)
            _log("➡️  التخطي (3 مرات) مع فواصل زمنية...")
            for i in range(3):
                ok = _try_click(page, [
                    "ytcp-button#next-button",
                    'ytcp-button:has-text("Next")',
                ], timeout=30000, force=True)
                if not ok:
                    _log(f"⚠️  Next {i+1} فشل")
                    break
                _log(f"   ⏸️  انتظار بعد Next {i+1}...")
                page.wait_for_timeout(5000)
                page.screenshot(path=f"{screenshot_dir}/next_{i+1}.png")

            # 6) 🎯 الآن نحن في شاشة Visibility
            _log("═" * 40)
            _log("🎯 وصلنا لشاشة Visibility + Made for Kids")
            page.wait_for_timeout(3000)
            _dump_radios(page, "بعد 3 next")
            page.screenshot(path=f"{screenshot_dir}/04_visibility_screen.png")

            # 7) اختر Made for Kids = No (إلزامي!)
            _select_made_for_kids_no(page)
            page.wait_for_timeout(2000)
            page.screenshot(path=f"{screenshot_dir}/05_kids_selected.png")

            # 8) اختر Public
            _select_public(page)
            page.wait_for_timeout(2000)
            page.screenshot(path=f"{screenshot_dir}/06_public_selected.png")

            # 9) انتظر الزر يتفعّل
            _wait_done_button_enabled(page, max_wait=30)
            page.screenshot(path=f"{screenshot_dir}/07_ready_to_publish.png")

            # 10) اضغط Publish
            _log("🚀 الضغط على Publish...")
            published = _try_click(page, [
                "ytcp-button#done-button",
                'ytcp-button:has-text("Publish")',
                'ytcp-button:has-text("Save")',
            ], timeout=30000, force=True)

            if not published:
                page.screenshot(path=f"{screenshot_dir}/08_no_publish.png")
                raise RuntimeError("❌ فشل النشر")

            # 11) انتظر تأكيد النجاح
            _log("⏳ انتظار تأكيد النشر...")
            page.wait_for_timeout(15000)
            page.screenshot(path=f"{screenshot_dir}/09_done.png")
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


if __name__ == "__main__":
    cookies_raw = os.getenv("YOUTUBE_COOKIES", "[]")
    upload_video(
        video_path="out/video.mp4",
        title=os.getenv("YT_TITLE", "اختبار رفع آلي — مالك فاكتوري"),
        description=os.getenv("YT_DESC", "رفع تجريبي من مصنع مالك."),
        cookies_raw=cookies_raw,
        headless=True,
    )
