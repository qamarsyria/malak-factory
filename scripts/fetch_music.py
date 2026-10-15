"""تحميل موسيقى ومؤثرات صوتية من Pixabay."""
import os
import json
import time
import re
from pathlib import Path
import requests

PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "").strip()

print("=" * 60)
print("🎵 تشخيص PIXABAY API")
print("=" * 60)
print(f"المفتاح موجود: {bool(PIXABAY_KEY)}")
print("=" * 60)


# ============================================================
# الموسيقى — 12 حالة (mood)
# ============================================================
MUSIC_MOODS = [
    ("calm",           "calm ambient music"),
    ("mystical",       "mystical mysterious music"),
    ("tense",          "tense suspense music"),
    ("sad",            "sad emotional music"),
    ("epic",           "epic cinematic music"),
    ("joyful",         "joyful uplifting music"),
    ("reflective",     "reflective contemplative music"),
    ("fearful",        "fearful dark ominous music"),
    ("peaceful",       "peaceful serene music"),
    ("dark",           "dark eerie music"),
    ("hope",           "hope inspiring music"),
    ("majestic",       "majestic sacred music"),
]


# ============================================================
# المؤثرات الصوتية — SFX
# ============================================================
SFX_QUERIES = [
    ("nature", [
        ("thunder",        "thunder storm"),
        ("rain",           "rain heavy"),
        ("wind-soft",      "wind soft breeze"),
        ("wind-night",     "night wind"),
        ("sea-waves",      "sea waves ocean"),
        ("sand-storm",     "desert sandstorm"),
        ("water-drip",     "water drop cave"),
        ("river",          "river flowing"),
    ]),
    ("whispers", [
        ("whisper-evil",   "evil whisper"),
        ("whisper-afraid", "fearful whisper"),
        ("whisper-amazed", "amazed whisper"),
    ]),
    ("battle", [
        ("horse-gallop",   "horse gallop"),
        ("sword-clash",    "sword clash metal"),
        ("footsteps",      "footsteps sand"),
    ]),
    ("environment", [
        ("door-wood",      "wooden door"),
        ("fire-crackle",   "fire crackle"),
    ]),
    ("heartbeat", [
        ("heartbeat-slow",  "heartbeat slow"),
        ("heartbeat-fast",  "heartbeat fast"),
        ("heartbeat-panic", "heartbeat panic"),
    ]),
]


# ============================================================
# المجلدات
# ============================================================
MUSIC_DIR = "assets/audio/music"
SFX_DIR = "assets/audio/sfx"
METADATA_FILE = "assets/audio/metadata.json"


def _search_audio(query, per_page=3):
    """يبحث في Pixabay Music + SFX."""
    # Pixabay يدعم audio ضمن نفس endpoint
    endpoint = "https://pixabay.com/api/audio/"
    params = {
        "key": PIXABAY_KEY,
        "q": query,
        "per_page": per_page,
    }
    try:
        r = requests.get(endpoint, params=params, timeout=30)
        if r.status_code == 404:
            # Fallback: نستخدم music endpoint التقليدي
            endpoint = "https://pixabay.com/api/"
            params["category"] = "music"
        if r.status_code != 200:
            print(f"   ⚠️  HTTP {r.status_code}: {r.text[:150]}")
            return []
        return r.json().get("hits", [])
    except Exception as e:
        print(f"   ⚠️  خطأ: {e}")
        return []


def _download(url, out_path):
    try:
        r = requests.get(url, timeout=60, stream=True)
        if r.status_code != 200:
            return False
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
        return Path(out_path).stat().st_size > 20480
    except Exception as e:
        print(f"   ⚠️  خطأ تحميل: {e}")
        return False


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch_music():
    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    metadata = []

    for mood, query in MUSIC_MOODS:
        print(f"\n🎵 بحث: {mood} ({query})")
        hits = _search_audio(query, per_page=3)
        print(f"   → {len(hits)} نتيجة")

        saved = 0
        for hit in hits[:2]:
            audio_id = hit.get("id")
            url = hit.get("audio") or hit.get("previewURL") or hit.get("url")
            if not url:
                continue

            filename = f"{mood}-{audio_id}.mp3"
            out_path = Path(MUSIC_DIR) / filename

            if out_path.exists():
                saved += 1
                continue

            if _download(url, out_path):
                metadata.append({
                    "id": audio_id,
                    "type": "music",
                    "mood": mood,
                    "filename": filename,
                    "url": url,
                })
                print(f"   ✅ {filename}")
                saved += 1
            time.sleep(0.5)

        if saved == 0:
            print(f"   ⚠️  لم تُحمّل موسيقى لـ {mood}")

    return metadata


def fetch_sfx():
    Path(SFX_DIR).mkdir(parents=True, exist_ok=True)
    metadata = []

    for category, items in SFX_QUERIES:
        cat_dir = Path(SFX_DIR) / category
        cat_dir.mkdir(parents=True, exist_ok=True)

        for sfx_name, query in items:
            print(f"\n🔊 بحث: {sfx_name} ({query})")
            hits = _search_audio(query, per_page=3)
            print(f"   → {len(hits)} نتيجة")

            for hit in hits[:1]:
                audio_id = hit.get("id")
                url = hit.get("audio") or hit.get("previewURL") or hit.get("url")
                if not url:
                    continue

                filename = f"{sfx_name}-{audio_id}.mp3"
                out_path = cat_dir / filename

                if out_path.exists():
                    continue

                if _download(url, out_path):
                    metadata.append({
                        "id": audio_id,
                        "type": "sfx",
                        "category": category,
                        "name": sfx_name,
                        "filename": f"{category}/{filename}",
                        "url": url,
                    })
                    print(f"   ✅ {filename}")
                time.sleep(0.5)

    return metadata


def main():
    if not PIXABAY_KEY:
        print("\n❌ لا يمكن المتابعة بدون مفتاح Pixabay")
        return

    print("\n🎼 الجزء 1: الموسيقى (12 حالة)")
    music_meta = fetch_music()

    print("\n🔊 الجزء 2: المؤثرات الصوتية")
    sfx_meta = fetch_sfx()

    all_meta = music_meta + sfx_meta
    Path(METADATA_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(METADATA_FILE).write_text(
        json.dumps(all_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print(f"✅ اكتمل: {len(music_meta)} موسيقى + {len(sfx_meta)} SFX")
    print(f"💾 {METADATA_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
