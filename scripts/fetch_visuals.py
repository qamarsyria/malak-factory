"""تحميل صور وفيديوهات من Pixabay — مرة واحدة فقط."""
import os
import json
import time
from pathlib import Path
import requests
from config import PIXABAY_KEY


# ============================================================
# كلمات البحث — لكل نوع من المشاهد
# ============================================================
SEARCH_QUERIES = {
    "images": [
        # قصص الأنبياء
        ("desert", 5),
        ("night sky stars", 3),
        ("sea waves storm", 4),
        ("rain storm clouds", 4),
        ("flood water", 3),
        ("mountains landscape", 3),
        ("river stream", 3),
        ("cave dark", 3),
        ("forest trees", 3),
        ("lightning thunder", 2),
        ("sand dunes", 3),
        ("well water", 2),
        ("palm trees oasis", 2),
        ("moon night", 3),
        ("sunrise sky", 3),
        ("quran book islamic", 2),
        ("mosque architecture", 3),
        ("islamic pattern", 2),
        ("light rays", 2),
        ("storm dark clouds", 3),
    ],
    "videos": [
        # فيديوهات للقصص الكبرى
        ("sea storm waves", 2),
        ("flood water rushing", 2),
        ("rain heavy storm", 2),
        ("desert sand storm", 2),
        ("clouds moving sky", 3),
        ("river flowing", 2),
        ("fire flames", 2),
        ("lightning", 2),
        ("stars night sky", 2),
        ("sunrise sunset", 2),
    ],
}


IMAGES_DIR = "assets/backgrounds/images"
VIDEOS_DIR = "assets/backgrounds/videos"
METADATA_FILE = "assets/backgrounds/metadata.json"


def _search_pixabay(query, media_type, per_page=5):
    """يبحث في Pixabay."""
    if media_type == "images":
        endpoint = "https://pixabay.com/api/"
        params = {
            "key": PIXABAY_KEY,
            "q": query,
            "image_type": "photo",
            "orientation": "vertical",
            "safesearch": "true",
            "per_page": per_page,
            "min_width": 1080,
        }
    else:  # videos
        endpoint = "https://pixabay.com/api/videos/"
        params = {
            "key": PIXABAY_KEY,
            "q": query,
            "video_type": "film",
            "safesearch": "true",
            "per_page": per_page,
            "min_width": 1080,
        }

    try:
        r = requests.get(endpoint, params=params, timeout=30)
        if r.status_code != 200:
            print(f"⚠️  HTTP {r.status_code} لـ {query}")
            return []
        return r.json().get("hits", [])
    except Exception as e:
        print(f"⚠️  خطأ بحث {query}: {e}")
        return []


def _download_file(url, out_path):
    """يحمّل ملف."""
    try:
        r = requests.get(url, timeout=60, stream=True)
        if r.status_code != 200:
            return False
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return Path(out_path).stat().st_size > 10240
    except Exception as e:
        print(f"⚠️  خطأ تحميل: {e}")
        return False


def _slugify(text):
    """يحوّل النص إلى اسم ملف صالح."""
    import re
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")


def fetch_images():
    """يحمّل الصور."""
    Path(IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    metadata = []

    for query, count in SEARCH_QUERIES["images"]:
        print(f"\n🖼️  البحث عن صور: {query}")
        hits = _search_pixabay(query, "images", per_page=count * 2)

        saved = 0
        for hit in hits[:count]:
            slug = _slugify(query)
            img_id = hit["id"]
            filename = f"{slug}-{img_id}.jpg"
            out_path = Path(IMAGES_DIR) / filename

            if out_path.exists():
                print(f"   ⏭️  موجود: {filename}")
                saved += 1
                continue

            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not url:
                continue

            if _download_file(url, out_path):
                metadata.append({
                    "id": img_id,
                    "type": "image",
                    "query": query,
                    "filename": filename,
                    "tags": hit.get("tags", ""),
                    "width": hit.get("imageWidth", 0),
                    "height": hit.get("imageHeight", 0),
                })
                print(f"   ✅ {filename}")
                saved += 1
            time.sleep(0.5)

        print(f"   → {saved}/{count} صورة")

    return metadata


def fetch_videos():
    """يحمّل الفيديوهات."""
    Path(VIDEOS_DIR).mkdir(parents=True, exist_ok=True)
    metadata = []

    for query, count in SEARCH_QUERIES["videos"]:
        print(f"\n🎬 البحث عن فيديو: {query}")
        hits = _search_pixabay(query, "videos", per_page=count * 2)

        saved = 0
        for hit in hits[:count]:
            slug = _slugify(query)
            vid_id = hit["id"]
            filename = f"{slug}-{vid_id}.mp4"
            out_path = Path(VIDEOS_DIR) / filename

            if out_path.exists():
                print(f"   ⏭️  موجود: {filename}")
                saved += 1
                continue

            videos = hit.get("videos", {})
            url = (videos.get("medium", {}).get("url")
                   or videos.get("small", {}).get("url"))

            if not url:
                continue

            if _download_file(url, out_path):
                metadata.append({
                    "id": vid_id,
                    "type": "video",
                    "query": query,
                    "filename": filename,
                    "tags": hit.get("tags", ""),
                    "duration": hit.get("duration", 0),
                })
                print(f"   ✅ {filename}")
                saved += 1
            time.sleep(0.5)

        print(f"   → {saved}/{count} فيديو")

    return metadata


def main():
    if not PIXABAY_KEY:
        print("❌ PIXABAY_KEY غير موجود")
        return

    print("=" * 60)
    print("📥 تحميل الخلفيات من Pixabay")
    print("=" * 60)

    print("\n📸 الجزء 1: الصور")
    images_meta = fetch_images()

    print("\n🎥 الجزء 2: الفيديوهات")
    videos_meta = fetch_videos()

    # حفظ metadata
    all_meta = images_meta + videos_meta
    Path(METADATA_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(METADATA_FILE).write_text(
        json.dumps(all_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print(f"✅ اكتمل: {len(images_meta)} صورة + {len(videos_meta)} فيديو")
    print(f"💾 metadata: {METADATA_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
