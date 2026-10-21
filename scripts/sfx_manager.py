"""مدير المؤثرات الصوتية — يبحث ويحمل من Freesound API."""
import os
import requests
import urllib.parse
from pathlib import Path
from config import FREESOUND_API_KEY, FREESOUND_CACHE_DIR

SFX_CACHE = Path(FREESOUND_CACHE_DIR)
SFX_CACHE.mkdir(parents=True, exist_ok=True)

def fetch_sfx(query: str) -> str:
    """يبحث عن مؤثر صوتي، يحمله (CC0)، ويعيد مساره المحلي."""
    if not FREESOUND_API_KEY:
        print("   ⚠️ لا يوجد Freesound API Key")
        return ""

    safe_query = query.replace(" ", "_")
    cache_path = SFX_CACHE / f"{safe_query}.mp3"

    # إذا كان محملاً مسبقاً، استخدمه
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        print(f"   🔊 (كاش) {query}")
        return str(cache_path)

    try:
        # 1. البحث
        search_url = f"https://freesound.org/apiv2/search/text/?query={urllib.parse.quote(query)}&filter=license:%22Creative%20Commons%200%22&fields=id,previews&page_size=1"
        headers = {"Authorization": f"Token {FREESOUND_API_KEY}"}
        
        r = requests.get(search_url, headers=headers, timeout=20)
        if r.status_code != 200:
            print(f"   ⚠️ فشل البحث عن {query}: HTTP {r.status_code}")
            return ""

        data = r.json()
        results = data.get("results", [])
        if not results:
            print(f"   ⚠️ لا نتائج لـ {query}")
            return ""

        # 2. تحميل الـ Preview
        preview_url = results[0].get("previews", {}).get("preview-hq-mp3")
        if not preview_url:
            return ""

        print(f"   🔊 تحميل {query}...")
        audio_resp = requests.get(preview_url, timeout=30)
        if audio_resp.status_code == 200:
            cache_path.write_bytes(audio_resp.content)
            print(f"   ✅ تم حفظ {query}")
            return str(cache_path)
    except Exception as e:
        print(f"   ⚠️ فشل تحميل {query}: {e}")

    return ""
