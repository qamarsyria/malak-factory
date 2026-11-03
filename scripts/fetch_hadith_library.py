import os
import json
import sys
import gzip
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "hadith-library.json.gz"

# روابط مباشرة لملفات JSON من Hugging Face
URLS = {
    "bukhari": "https://huggingface.co/datasets/meeAtif/hadith_datasets/resolve/main/Sahih%20al-Bukhari.json",
    "muslim": "https://huggingface.co/datasets/meeAtif/hadith_datasets/resolve/main/Sahih%20Muslim.json"
}

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("⬇️ جاري تحميل مكتبة الحديث (صحيح البخاري وصحيح مسلم)...")
    
    all_hadiths = []
    
    for book_name, url in URLS.items():
        print(f"📥 تحميل {book_name}...")
        try:
            r = requests.get(url, timeout=60)
            if r.status_code != 200:
                print(f"❌ فشل تحميل {book_name}: HTTP {r.status_code}")
                continue
                
            data = r.json()
            for idx, item in enumerate(data):
                # استخراج النص العربي والإسناد
                text = item.get("Arabic_Text", "").strip()
                if not text: continue
                
                all_hadiths.append({
                    "id": f"{book_name}-{idx+1:05d}",
                    "text": text,
                    "book": item.get("Book", book_name),
                    "chapter": item.get("Chapter_Title_Arabic", ""),
                    "grade": item.get("Grade", "صحيح"),
                    "reference": item.get("Reference", ""),
                    "type": "hadith"
                })
            print(f"✅ تم تحميل {book_name} ({len(data)} حديث).")
        except Exception as e:
            print(f"❌ خطأ في تحميل {book_name}: {e}")
    
    print(f"📝 إجمالي الأحاديث التي تم تجميعها: {len(all_hadiths)}")
    
    # حفظ الملف مضغوطاً
    print("🗜️ جاري ضغط الملف...")
    with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
        json.dump(all_hadiths, f, ensure_ascii=False)
        
    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"✅ تم حفظ مكتبة الحديث بنجاح في: {OUTPUT_FILE} (الحجم: {size_mb:.1f} ميجابايت)")

if __name__ == "__main__":
    main()
