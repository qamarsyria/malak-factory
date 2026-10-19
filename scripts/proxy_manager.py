# scripts/proxy_manager.py
import requests
import random
import time

def _log(msg):
    print(f"[ProxyManager] {msg}", flush=True)

def get_working_proxy() -> str | None:
    """يجلب ويختبر البروكسيات ويعيد أول بروكسي صالح."""
    
    # قائمة بالمصادر الموثوقة التي يتم تحديثها باستمرار
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
                    # إضافة البروتوكول إذا كان مفقوداً
                    all_proxies.add(f"http://{line.strip()}")
        except Exception as e:
            _log(f"⚠️ فشل جلب من {url}: {e}")
            continue

    if not all_proxies:
        _log("❌ لم يتم العثور على أي بروكسيات من المصادر.")
        return None

    proxy_list = list(all_proxies)
    random.shuffle(proxy_list)
    _log(f"تم جمع {len(proxy_list)} بروكسي. جاري اختبارها...")

    # اختبار أول 50 بروكسي (لتوفير الوقت)
    for proxy in proxy_list[:50]:
        try:
            _log(f"  -> اختبار: {proxy}")
            # اختبار الوصول إلى يوتيوب
            test_url = "https://www.youtube.com/generate_204"
            proxies = {"http": proxy, "https": proxy}
            r = requests.get(test_url, proxies=proxies, timeout=10, allow_redirects=False)
            
            if r.status_code in [204, 200]:
                _log(f"✅ نجح البروكسي: {proxy}")
                return proxy
            else:
                _log(f"  -> فشل (رمز الحالة: {r.status_code})")
        except Exception:
            continue
            
    _log("❌ لم يتم العثور على أي بروكسي صالح من القائمة المختبرة.")
    return None
