import gzip
import json
import random
import sys
import time
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "hadith-library.json.gz"

HADEETHENC_BASE = "https://hadeethenc.com/api/v1"
ALLOWED_GRADES = ("صحيح", "حسن", "متفق عليه")


def fetch_from_hadeethenc() -> list[dict]:
    print("⬇️ جاري تحميل موسوعة الأحاديث النبوية وشروحها (HadeethEnc.com)...")
    session = requests.Session()
    session.headers.update({"User-Agent": "MalakAlMarifaBot/1.0"})

    try:
        r_cats = session.get(f"{HADEETHENC_BASE}/categories/list/?language=ar", timeout=20)
        r_cats.raise_for_status()
        categories = r_cats.json()
    except Exception as e:
        print(f"   ⚠️ تعذر جلب أقسام HadeethEnc: {e}")
        return []

    if not isinstance(categories, list):
        return []

    random.shuffle(categories)

    # جمع أحاديث متنوعة من جميع الأقسام
    stubs: dict[str, dict] = {}
    for cat in categories:
        cat_id = cat.get("id")
        cat_title = cat.get("title", "")
        if not cat_id:
            continue
        try:
            r_list = session.get(
                f"{HADEETHENC_BASE}/hadeeths/list/",
                params={"language": "ar", "category_id": cat_id, "page": 1, "per_page": 25},
                timeout=20,
            )
            if r_list.status_code != 200:
                continue
            data = r_list.json().get("data", [])
            for row in data:
                hid = str(row.get("id", "")).strip()
                if hid and hid not in stubs:
                    stubs[hid] = {
                        "id": f"henc-{hid}",
                        "henc_id": hid,
                        "title": (row.get("title") or "").strip(),
                        "category": cat_title,
                    }
            if len(stubs) >= 1200:
                break
        except Exception:
            continue

    print(f"   📚 تم حصر {len(stubs)} حديث من مختلف الأبواب، جاري تحميل الشروح والتخريج لـ 250 حديثاً...")
    all_ids = list(stubs.keys())
    random.shuffle(all_ids)
    detailed_map: dict[str, dict] = {}

    for i in range(0, min(len(all_ids), 250), 25):
        batch = all_ids[i : i + 25]
        try:
            r_mul = session.get(
                f"{HADEETHENC_BASE}/hadeeths/multiple/",
                params={"language": "ar", "ids": ",".join(batch)},
                timeout=25,
            )
            if r_mul.status_code == 200:
                rows = r_mul.json()
                if isinstance(rows, list):
                    for item in rows:
                        hid = str(item.get("id", "")).strip()
                        grade = (item.get("grade") or "").strip()
                        if grade and not any(g in grade for g in ALLOWED_GRADES):
                            continue
                        if hid and item.get("hadeeth") and item.get("explanation"):
                            detailed_map[hid] = item
            time.sleep(0.25)
        except Exception:
            continue

    results = []
    for hid, stub in stubs.items():
        if hid in detailed_map:
            d = detailed_map[hid]
            results.append({
                "id": stub["id"],
                "henc_id": hid,
                "title": d.get("title") or stub["title"],
                "category": stub["category"],
                "text": (d.get("hadeeth") or "").strip(),
                "attribution": (d.get("attribution") or "").strip(),
                "grade": (d.get("grade") or "صحيح").strip(),
                "explanation": (d.get("explanation") or "").strip(),
                "hints": d.get("hints") or [],
                "words_meanings": d.get("words_meanings") or [],
                "source": "موسوعة الأحاديث النبوية HadeethEnc.com",
                "type": "hadith",
            })
        else:
            results.append({
                "id": stub["id"],
                "henc_id": hid,
                "title": stub["title"],
                "category": stub["category"],
                "text": stub["title"],
                "attribution": "",
                "grade": "صحيح",
                "explanation": "",
                "hints": [],
                "words_meanings": [],
                "source": "موسوعة الأحاديث النبوية HadeethEnc.com",
                "type": "hadith",
            })

    print(f"   ✅ جاهز: {len(results)} حديث ({len(detailed_map)} بشرح كامل وتخريج محلياً).")
    return results


def fetch_bukhari_muslim_backup() -> list[dict]:
    print("⬇️ تحميل النسخة الاحتياطية (صحيح البخاري وصحيح مسلم)...")
    urls = {
        "صحيح البخاري": "https://huggingface.co/datasets/meeAtif/hadith_datasets/resolve/main/Sahih%20al-Bukhari.json",
        "صحيح مسلم": "https://huggingface.co/datasets/meeAtif/hadith_datasets/resolve/main/Sahih%20Muslim.json",
    }
    items = []
    for book_name, url in urls.items():
        try:
            r = requests.get(url, timeout=60)
            if r.status_code != 200:
                continue
            for idx, item in enumerate(r.json()):
                text = (item.get("Arabic_Text") or "").strip()
                if not text or len(text) > 1500:
                    continue
                items.append({
                    "id": f"sahih-{book_name}-{idx+1:05d}",
                    "henc_id": "",
                    "title": item.get("Chapter_Title_Arabic") or book_name,
                    "category": book_name,
                    "text": text,
                    "attribution": book_name,
                    "grade": item.get("Grade") or "صحيح",
                    "explanation": "",
                    "hints": [],
                    "words_meanings": [],
                    "source": book_name,
                    "type": "hadith",
                })
        except Exception as e:
            print(f"   ⚠️ خطأ في {book_name}: {e}")
    return items


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    hadiths = fetch_from_hadeethenc()
    if not hadiths:
        hadiths = fetch_bukhari_muslim_backup()

    if not hadiths:
        print("❌ فشل تحميل مكتبة الحديث.")
        sys.exit(1)

    with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
        json.dump(hadiths, f, ensure_ascii=False)

    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"✅ تم حفظ مكتبة الحديث بنجاح: {OUTPUT_FILE} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
