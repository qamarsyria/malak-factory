"""مدير بروكسيات سورية ذكي — مصادر متعددة + اختبار متوازي + Gemini Search."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import aiohttp
import requests


def _log(msg: str) -> None:
    print(f"[proxy] {msg}", flush=True)


# ═══════════════════════════════════════════════════════
#  مصادر البروكسيات (APIs عامة مجانية)
# ═══════════════════════════════════════════════════════

PROXY_SOURCES = [
    # proxyscrape - API نظيف
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=sy&protocol=http&proxy_format=ipport&format=text",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=sy&protocol=socks5&proxy_format=ipport&format=text",
    # TheSpeedX - قوائم GitHub
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    # monosans - قوائم محدثة
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    # clarketm
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    # shiftytr
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/proxy-list.txt",
    # hookzof
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
]

# نطاقات IPv4 المخصصة لسوريا (لمعرفة البروكسي السوري)
# (مصدر: RIPE NCC — نطاقات مسجلة لسوريا)
SYRIAN_IP_RANGES = [
    "5.0.0.0/16", "31.9.0.0/16", "37.48.0.0/16", "46.53.0.0/16",
    "62.3.0.0/16", "77.30.0.0/16", "78.155.0.0/16", "82.137.0.0/16",
    "89.43.0.0/16", "94.141.0.0/16", "95.85.0.0/16", "109.108.0.0/16",
    "128.0.0.0/16", "141.0.0.0/16", "176.29.0.0/16", "178.172.0.0/16",
    "185.225.0.0/16", "188.128.0.0/16", "193.43.0.0/16", "195.89.0.0/16",
    "195.234.0.0/16", "213.178.0.0/16", "217.29.0.0/16", "217.68.0.0/16",
]


def _is_syrian_ip(ip: str) -> bool:
    """يتحقق تقريبياً إذا كان الـ IP ضمن نطاقات سوريا."""
    try:
        parts = [int(x) for x in ip.split(".")]
        if len(parts) != 4:
            return False
        first, second = parts[0], parts[1]
        # فحص النطاقات الشائعة لسوريا
        for rng in SYRIAN_IP_RANGES:
            r_first, r_second, _ = rng.split("/")[0].split(".")
            if int(r_first) == first and int(r_second) == second:
                return True
        return False
    except Exception:
        return False


# ═══════════════════════════════════════════════════════
#  Cache (state file)
# ═══════════════════════════════════════════════════════

CACHE_FILE = Path("state/working-proxy.json")


def _load_cache() -> list[str]:
    """يقرأ البروكسيات الناجحة من الـ cache."""
    if not CACHE_FILE.exists():
        return []
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        # نتجاهل اللي أقدم من 6 ساعات
        if time.time() - data.get("ts", 0) > 21600:
            return []
        return data.get("proxies", [])
    except Exception:
        return []


def _save_cache(proxies: list[str]) -> None:
    """يحفظ البروكسيات الناجحة."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"proxies": proxies, "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════
#  جلب البروكسيات من المصادر
# ═══════════════════════════════════════════════════════

def _fetch_from_source(url: str, timeout: int = 15) -> list[str]:
    """يجلب قائمة بروكسيات من مصدر واحد."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        lines = r.text.splitlines()
        proxies = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # استخرج IP:PORT
            m = re.match(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$", line)
            if m:
                ip, port = m.group(1), m.group(2)
                # فلترة: نطاقات سورية فقط
                if _is_syrian_ip(ip):
                    proxies.append(f"{ip}:{port}")
        return proxies
    except Exception as e:
        _log(f"⚠️  فشل {url[:60]}: {str(e)[:60]}")
        return []


def _gather_from_sources() -> list[str]:
    """يجمع البروكسيات من كل المصادر بالتوازي."""
    _log(f"📡 نجمع من {len(PROXY_SOURCES)} مصدر بالتوازي...")

    import concurrent.futures
    all_proxies: set[str] = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_from_source, url) for url in PROXY_SOURCES]
        for f in concurrent.futures.as_completed(futures, timeout=30):
            try:
                result = f.result()
                all_proxies.update(result)
            except Exception:
                pass

    proxies = list(all_proxies)
    _log(f"✅ جمعنا {len(proxies)} بروكسي سوري فريد")
    return proxies


# ═══════════════════════════════════════════════════════
#  Gemini Search Grounding (لاكتشاف مصادر جديدة)
# ═══════════════════════════════════════════════════════

def _ask_gemini_for_sources() -> list[str]:
    """
    يستخدم Gemini مع Search Grounding ليبحث عن مصادر جديدة للبروكسيات السورية.
    يرجع قائمة URLs جديدة.
    """
    _log("🧠 نسأل Gemini عن مصادر بروكسيات سورية...")

    keys_raw = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
    keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    if not keys:
        return []

    prompt = """ابحث عن روابط حديثة (2026) لقوائم بروكسيات سورية (Syrian proxies) مجانية ومحدثة.

**الشروط:**
- روابط مباشرة لملفات نصية (txt) أو APIs ترجع IP:PORT
- من مصادر موثوقة ومحدثة أسبوعياً
- تفضل روابط raw.githubusercontent.com أو APIs مجانية

**مطلوب:** اكتب فقط قائمة روابط (URL واحد لكل سطر)، بدون شرح ولا تنسيق.
10 روابط كحد أقصى، مرتّبة حسب الموثوقية."""

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],  # Search Grounding
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 800,
        },
    }

    for key in keys[:3]:  # جرّب 3 مفاتيح فقط
        for model in ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-flash-latest"]:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={key}",
                    json=body,
                    timeout=45,
                )
                if r.status_code != 200:
                    continue

                data = r.json()
                cands = data.get("candidates") or []
                if not cands:
                    continue

                parts = (cands[0].get("content") or {}).get("parts") or []
                txt = ""
                for p in parts:
                    if "text" in p:
                        txt += p["text"]

                # استخرج URLs
                urls = re.findall(r'https?://[^\s\n\)\]"\']+', txt)
                # فلترة الروابط المفيدة
                good = [
                    u for u in urls
                    if any(k in u.lower() for k in [
                        "proxy", "raw.githubusercontent", "github", "api"
                    ])
                ][:10]

                if good:
                    _log(f"✅ Gemini اقترح {len(good)} مصدر جديد")
                    for u in good:
                        _log(f"   → {u[:80]}")
                    return good

            except Exception as e:
                _log(f"⚠️  {model}: {str(e)[:60]}")
                continue

    _log("⚠️  Gemini لم يقدم مصادر جديدة")
    return []


# ═══════════════════════════════════════════════════════
#  اختبار البروكسيات بالتوازي
# ═══════════════════════════════════════════════════════

async def _test_proxy_async(session: aiohttp.ClientSession, proxy: str) -> tuple[str, bool, float]:
    """يختبر بروكسي واحد بشكل غير متزامن."""
    start = time.time()
    try:
        # نجرّب HTTP أولاً (الأكثر شيوعاً)
        for scheme in ["http", "socks5", "socks4"]:
            try:
                proxy_url = f"{scheme}://{proxy}"
                async with session.get(
                    "https://www.youtube.com/generate_204",
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    allow_redirects=False,
                ) as resp:
                    if resp.status in (204, 200):
                        elapsed = time.time() - start
                        return (f"{scheme}://{proxy}", True, elapsed)
            except Exception:
                continue
        return (proxy, False, time.time() - start)
    except Exception:
        return (proxy, False, time.time() - start)


async def _test_proxies_parallel(proxies: list[str], max_test: int = 60) -> list[str]:
    """يختبر قائمة بروكسيات بالتوازي."""
    if not proxies:
        return []

    # خذ أول N فقط لتوفير الوقت
    proxies = proxies[:max_test]
    _log(f"🔬 نختبر {len(proxies)} بروكسي بالتوازي...")

    connector = aiohttp.TCPConnector(limit=40, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [_test_proxy_async(session, p) for p in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    working = []
    for r in results:
        if isinstance(r, Exception):
            continue
        proxy_url, ok, elapsed = r
        if ok:
            working.append((proxy_url, elapsed))
            _log(f"   ✅ {proxy_url}  ({elapsed:.1f}s)")

    # رتّب حسب السرعة
    working.sort(key=lambda x: x[1])
    return [w[0] for w in working]


# ═══════════════════════════════════════════════════════
#  الواجهة الرئيسية
# ═══════════════════════════════════════════════════════

_FOUND_PROXY: dict | None = None  # cache في الجلسة


def find_working_proxy(force_refresh: bool = False) -> dict | None:
    """
    يجد بروكسي سوري يعمل.
    - يجرب cache أولاً
    - يجمع من مصادر ثابتة
    - يستعين بـ Gemini لاكتشاف مصادر جديدة
    - يختبر كل شي بالتوازي
    - يحفظ الناجح
    """
    global _FOUND_PROXY

    if _FOUND_PROXY is not None and not force_refresh:
        return _FOUND_PROXY

    _log("═" * 50)
    _log("🔍 البحث عن بروكسي سوري شغّال")

    # ═══ 1) جرّب الـ cache أولاً ═══
    cached = _load_cache()
    if cached:
        _log(f"💾 {len(cached)} بروكسي من cache — نختبرها...")
        working = asyncio.run(_test_proxies_parallel(cached, max_test=20))
        if working:
            best = working[0]
            _log(f"🎉 cache نجح: {best}")
            _FOUND_PROXY = {"server": best}
            _save_cache(working)
            return _FOUND_PROXY

    # ═══ 2) نجمع من المصادر الثابتة ═══
    proxies = _gather_from_sources()

    # ═══ 3) نختبرها بالتوازي ═══
    working: list[str] = []
    if proxies:
        working = asyncio.run(_test_proxies_parallel(proxies, max_test=60))

    # ═══ 4) إذا فشلت، نسأل Gemini ═══
    if not working:
        _log("⚠️  المصادر الثابتة فشلت — نسأل Gemini...")
        new_sources = _ask_gemini_for_sources()

        for url in new_sources:
            extra = _fetch_from_source(url)
            if extra:
                _log(f"📡 من {url[:60]}: {len(extra)} بروكسي")
                working = asyncio.run(
                    _test_proxies_parallel(extra, max_test=40)
                )
                if working:
                    break

    # ═══ 5) النتيجة ═══
    if not working:
        _log("❌ لم نجد أي بروكسي سوري شغّال")
        return None

    # خذ الأفضل (الأسرع)
    best = working[0]
    _log(f"🎉 أفضل بروكسي: {best}")

    _save_cache(working)  # احفظ كل اللي نجحوا
    _FOUND_PROXY = {"server": best}
    return _FOUND_PROXY


if __name__ == "__main__":
    # اختبار مباشر
    result = find_working_proxy()
    if result:
        print(f"\n✅ بروكسي: {result['server']}")
    else:
        print("\n❌ لا بروكسي")
