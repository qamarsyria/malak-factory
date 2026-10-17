"""مدير بروكسيات سورية ذكي — مصادر متعددة + اختبار متوازي + Gemini Search."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

import aiohttp
import requests


def _log(msg: str) -> None:
    print(f"[proxy] {msg}", flush=True)


PROXY_SOURCES = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=sy&protocol=http&proxy_format=ipport&format=text",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=sy&protocol=socks5&proxy_format=ipport&format=text",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/proxy-list.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
]

def _is_syrian_ip(ip: str) -> bool:
    # معطّل مؤقتاً لأن API يضمن الدولة
    return True

CACHE_FILE = Path("state/working-proxy.json")

def _load_cache() -> list[str]:
    if not CACHE_FILE.exists():
        return []
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > 21600:
            return []
        return data.get("proxies", [])
    except Exception:
        return []

def _save_cache(proxies: list[str]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"proxies": proxies, "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )

def _fetch_from_source(url: str, timeout: int = 15) -> list[str]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        proxies = []
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$", line)
            if m:
                ip, port = m.group(1), m.group(2)
                if _is_syrian_ip(ip):
                    proxies.append(f"{ip}:{port}")
        return proxies
    except Exception as e:
        _log(f"⚠️  فشل {url[:60]}: {str(e)[:60]}")
        return []

def _gather_from_sources() -> list[str]:
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
    _log(f"✅ جمعنا {len(proxies)} بروكسي فريد")
    return proxies

def _ask_gemini_for_sources() -> list[str]:
    _log("🧠 نسأل Gemini عن مصادر بروكسيات سورية...")
    keys_raw = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
    keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    if not keys:
        return []

    prompt = """ابحث عن روابط حديثة (2026) لقوائم بروكسيات سورية (Syrian proxies) مجانية ومحدثة.
الشروط: روابط مباشرة لملفات نصية (txt) أو APIs ترجع IP:PORT.
مطلوب: اكتب فقط قائمة روابط (URL واحد لكل سطر)، بدون شرح.
10 روابط كحد أقصى، مرتّبة حسب الموثوقية."""

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
    }

    for key in keys[:3]:
        for model in ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-flash-latest"]:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                    json=body, timeout=45,
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                cands = data.get("candidates") or []
                if not cands:
                    continue
                txt = "".join(p["text"] for p in (cands[0].get("content") or {}).get("parts") or [] if "text" in p)
                urls = re.findall(r'https?://[^\s\n\)\]"\']+', txt)
                good = [u for u in urls if any(k in u.lower() for k in ["proxy", "raw.githubusercontent", "github", "api"])][:10]
                if good:
                    _log(f"✅ Gemini اقترح {len(good)} مصدر جديد")
                    return good
            except Exception:
                continue
    return []

async def _test_proxy_async(session: aiohttp.ClientSession, proxy: str) -> tuple[str, bool, float]:
    start = time.time()
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
                    return (f"{scheme}://{proxy}", True, time.time() - start)
        except Exception:
            continue
    return (proxy, False, time.time() - start)

async def _test_proxies_parallel(proxies: list[str], max_test: int = 60) -> list[str]:
    if not proxies:
        return []
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
            _log(f"   ✅ {proxy_url} ({elapsed:.1f}s)")

    working.sort(key=lambda x: x[1])
    return [w[0] for w in working]

_FOUND_PROXY: dict | None = None

def find_working_proxy(force_refresh: bool = False) -> dict | None:
    global _FOUND_PROXY
    if _FOUND_PROXY is not None and not force_refresh:
        return _FOUND_PROXY

    _log("═" * 50)
    _log("🔍 البحث عن بروكسي شغّال")

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

    proxies = _gather_from_sources()
    working: list[str] = []
    if proxies:
        working = asyncio.run(_test_proxies_parallel(proxies, max_test=60))

    if not working:
        _log("⚠️  المصادر الثابتة فشلت — نسأل Gemini...")
        new_sources = _ask_gemini_for_sources()
        for url in new_sources:
            extra = _fetch_from_source(url)
            if extra:
                working = asyncio.run(_test_proxies_parallel(extra, max_test=40))
                if working:
                    break

    if not working:
        _log("❌ لم نجد أي بروكسي شغّال")
        return None

    best = working[0]
    _log(f"🎉 أفضل بروكسي: {best}")
    _save_cache(working)
    _FOUND_PROXY = {"server": best}
    return _FOUND_PROXY
