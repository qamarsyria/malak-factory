import os

# مسح أي HF_TOKEN منتهي الصلاحية من البيئة قبل تحميل المكتبات العامة
for _var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_HUB_TOKEN"):
    os.environ.pop(_var, None)

import csv
import gzip
import json
import re
import sys
from pathlib import Path
import requests
from datasets import load_dataset
from huggingface_hub import hf_hub_download

csv.field_size_limit(10_000_000)

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "fiqh-library.json.gz"


def clean_raw(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _download_binbaz_csv() -> Path:
    try:
        p = hf_hub_download(
            repo_id="ZenoAbderrahim/binbaz_fatwas_rag",
            filename="binbaz_clean.csv",
            repo_type="dataset",
            token=False,
        )
        return Path(p)
    except Exception:
        url = "https://huggingface.co/datasets/ZenoAbderrahim/binbaz_fatwas_rag/resolve/main/binbaz_clean.csv"
        tmp_csv = OUTPUT_DIR / "_binbaz_clean.csv"
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp_csv, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        return tmp_csv


def load_ibn_baz() -> list[dict]:
    items = []
    print("⬇️ جاري تحميل فتاوى سماحة الشيخ ابن باز رحمه الله (binbaz.org.sa)...")
    tmp_csv = OUTPUT_DIR / "_binbaz_clean.csv"
    try:
        csv_path = _download_binbaz_csv()
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fid = str(row.get("fatwa_id", "")).strip()
                title = clean_raw(row.get("title", ""))
                question = clean_raw(row.get("question", "")) or title
                answer = clean_raw(row.get("answer", ""))
                url = clean_raw(row.get("links", ""))
                cats = clean_raw(row.get("categories", ""))

                if len(question) < 12 or len(answer) < 120 or len(answer) > 6500:
                    continue

                items.append({
                    "id": f"binbaz-{fid or len(items)+1}",
                    "title": title or question[:80],
                    "question": question,
                    "answer": answer,
                    "scholar": "سماحة الشيخ عبد العزيز بن باز رحمه الله",
                    "source": "الموقع الرسمي للشيخ ابن باز",
                    "categories": cats,
                    "url": url,
                    "type": "fiqh",
                })
        print(f"   ✅ تم جمع {len(items)} فتوى للشيخ ابن باز.")
    except Exception as e:
        print(f"   ⚠️ تعذر تحميل فتاوى ابن باز: {e}")
    finally:
        if tmp_csv.exists():
            tmp_csv.unlink(missing_ok=True)
    return items


def load_uthaymeen_and_salaf_books() -> list[dict]:
    items = []
    print("⬇️ جاري تحميل فتاوى نور على الدرب لابن عثيمين وكتب السلف...")
    try:
        ds = load_dataset("abdullah-alamodi/aqeedah-rag-dataset", split="train", token=False)
        for idx, row in enumerate(ds):
            text = clean_raw(row.get("paragraph_text", ""))
            doc_name = clean_raw(row.get("doc_name", ""))
            author = clean_raw(row.get("author_name", ""))

            if len(text) < 250 or len(text) > 4500:
                continue
            if "فهرس" in doc_name or "المحتويات" in text[:40]:
                continue

            items.append({
                "id": f"salaf-{idx+1:05d}",
                "title": doc_name or "مسألة شرعية",
                "question": f"فائدة ومسألة من كتاب {doc_name}" if doc_name else "مسألة شرعية",
                "answer": text,
                "scholar": author or "من علماء أهل السنة والجماعة",
                "source": f"{doc_name} ({author})" if author else doc_name,
                "categories": doc_name,
                "url": "",
                "type": "fiqh",
            })
        print(f"   ✅ تم جمع {len(items)} فقرة علمية سلفية.")
    except Exception as e:
        print(f"   ⚠️ تعذر تحميل مكتبة كتب السلف وابن عثيمين: {e}")
    return items


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_items = []
    all_items.extend(load_ibn_baz())
    all_items.extend(load_uthaymeen_and_salaf_books())

    if not all_items:
        print("❌ فشل جمع مكتبة الفقه من جميع المصادر.")
        sys.exit(1)

    print(f"📝 إجمالي المواد الفقهية والعلمية: {len(all_items)}")
    with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False)

    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"✅ تم حفظ مكتبة الفقه بنجاح: {OUTPUT_FILE} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
