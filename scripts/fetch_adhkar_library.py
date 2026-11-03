import os
import json
import sys
import gzip
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "adhkar-library.json.gz"

URL = "https://raw.githubusercontent.com/osamayy/azkar-db/master/azkar.json"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("⬇️ جاري تحميل مكتبة الأذكار والأدعية...")
    
    try:
        r = requests.get(URL, timeout=30)
        if r.status_code != 200:
            print(f"❌ فشل التحميل: HTTP {r.status_code}")
            sys.exit(1)
            
        data = r.json()
        adhkar_list = []
        
        for idx, item in enumerate(data):
            zekr = item.get("zekr", "").strip()
            if not zekr: continue
            
            adhkar_list.append({
                "id": f"adhkar-{idx+1:05d}",
                "text": zekr,
                "category": item.get("category", ""),
                "count": item.get("count", ""),
                "reference": item.get("reference", ""),
                "type": "adhkar"
            })
            
        print(f"📝 تم تجميع {len(adhkar_list)} ذكر/دعاء.")
        
        print("🗜️ جاري ضغط الملف...")
        with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
            json.dump(adhkar_list, f, ensure_ascii=False)
            
        size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
        print(f"✅ تم حفظ مكتبة الأذكار بنجاح في: {OUTPUT_FILE} (الحجم: {size_mb:.1f} ميجابايت)")
        
    except Exception as e:
        print(f"❌ حدث خطأ: {e}")

if __name__ == "__main__":
    main()
