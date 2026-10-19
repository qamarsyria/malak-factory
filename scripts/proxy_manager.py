# scripts/proxy_manager.py
import requests
import random
import time
from playwright.sync_api import sync_playwright

def _log(msg):
    print(f"[ProxyManager] {msg}", flush=True)

def _test_with_requests(proxy: str) -> bool:
    """اختبار سريع عبر requests للتأكد من أن البروكسي يفتح m.youtube.com"""
    try:
        test_url = "https://m.youtube.com/"
        proxies = {"http": proxy, "https": proxy}
        start = time.time()
        r = requests.get(test_url, proxies=proxies, timeout=8, allow_redirects=True)
        elapsed = time.time() - start
        if r.status_code == 200 and elapsed < 8:
            _log(f"  -> ✅ requests نجح: {proxy} ({elapsed:.1f}ث)")
            return True
        return False
    except Exception as e:
        _log(f"  -> ❌ requests فشل: {proxy} ({str(e)[:50]})")
        return False

def _test_with_playwright(proxy: str) -> bool:
    """اختبار حقيقي عبر Playwright (نفس المتصفح الذي سيستخدمه البوت)"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, proxy={"server": proxy})
            page = browser.new_page()
            page.goto("https://m.youtube.com/", wait_until="domcontentloaded", timeout=15000)
            # تحقق من وجود عنصر يدل على أن الصفحة تحملت
            if page.locator('ytm-app').count() > 0:
                browser.close()
                _log(f"  -> 🎉 Playwright نجح: {proxy}")
                return True
            browser.close()
            return False
    except Exception as e:
        _log(f"  -> ❌ Playwright فشل: {proxy} ({str(e)[:80]})")
        return False

def get_working_proxy() -> str | None:
    """يجلب ويختبر البروكسيات ويعيد أول بروكسي صالح."""
    
    sources = [
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/https/data.txt",
        "https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list@main/proxies/all/data.txt",
    ]
    
    all_proxies = set()
    for url in sources:
        try:
            _log(f"جاري جلب البروكسيات من: {url}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            for line in response.text.strip().split('\n'):
                if '://' in line:
                    all_proxies.add(line.strip())
                else:
                    all_proxies.add(f"http://{line.strip()}")
        except Exception as e:
            _log(f"⚠️ فشل جلب من {url}: {e}")
            continue

    if not all_proxies:
        _log("❌ لم يتم العثور على أي بروكسيات من المصادر.")
        return None

    proxy_list = list(all_proxies)
    random.shuffle(proxy_list)
    _log(f"تم جمع {len(proxy_list)} بروكسي. جاري الفلترة السريعة بـ requests...")

    # 1. فلترة سريعة بـ requests (نجمع أول 5 بروكسيات ناجحة)
    candidate_proxies = []
    for proxy in proxy_list:
        if len(candidate_proxies) >= 5:
            break
        if "socks" in proxy.lower():
            continue  # نتخطى SOCKS لأنها تحتاج مكتبات إضافية
        _log(f"  -> اختبار requests: {proxy}")
        if _test_with_requests(proxy):
            candidate_proxies.append(proxy)

    if not candidate_proxies:
        _log("❌ لم يتم العثور على أي بروكسي ينجح في الفلترة السريعة.")
        return None

    _log(f"✅ {len(candidate_proxies)} بروكسي نجح في الفلترة السريعة. جاري الاختبار الحقيقي بـ Playwright...")

    # 2. اختبار حقيقي بـ Playwright
    for proxy in candidate_proxies:
        _log(f"  -> اختبار Playwright: {proxy}")
        if _test_with_playwright(proxy):
            return proxy

    _log("❌ لم يتم العثور على أي بروكسي ينجح في الاختبار الحقيقي.")
    return None
