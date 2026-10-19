# scripts/proxy_manager.py
import requests
import random
import time

def _log(msg):
    print(f"[ProxyManager] {msg}", flush=True)

def get_working_proxy() -> str | None:
    """يجلب ويختبر البروكسيات ويعيد أول بروكسي صالح وسريع."""
    
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
    _log(f"تم جمع {len(proxy_list)} بروكسي. جاري اختبارها (أول 30 فقط)...")

    # اختبار أول 30 بروكسي فقط (لتوفير الوقت)
    for proxy in proxy_list[:30]:
        try:
            _log(f"  -> اختبار: {proxy}")
            # اختبار حقيقي: تحميل صفحة يوتيوب
            test_url = "https://www.youtube.com/"
            proxies = {"http": proxy, "https": proxy}
            
            # شرط: يجب أن يستغرق أقل من 8 ثوانٍ
            start_time = time.time()
            r = requests.get(test_url, proxies=proxies, timeout=8, allow_redirects=True)
            elapsed = time.time() - start_time
            
            if r.status_code == 200 and elapsed < 8:
                _log(f"✅ نجح البروكسي: {proxy} (زمن: {elapsed:.1f}ث)")
                return proxy
            else:
                _log(f"  -> فشل (رمز: {r.status_code}, زمن: {elapsed:.1f}ث)")
        except Exception as e:
            _log(f"  -> فشل: {str(e)[:60]}")
            continue
            
    _log("❌ لم يتم العثور على أي بروكسي صالح.")
    return None
