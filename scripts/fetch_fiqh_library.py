import os
import json
import sys
import gzip
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "adhkar-library.json.gz"

# الرابط المباشر الصحيح (من مستودع rn0x/Adhkar-json)
URL = "https://raw.githubusercontent.com/rn0x/Adhkar-json/main/adhkar.json"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("⬇️ جاري تحميل مكتبة الأذكار والأدعية (حصن المسلم)...")
    
    try:
        r = requests.get(URL, timeout=30)
        if r.status_code != 200:
            print(f"❌ فشل التحميل: HTTP {r.status_code}")
            sys.exit(1) # إيقاف العملية بإخطار
            
        data = r.json()
        adhkar_list = []
        
        # بنية الملف: قائمة من الفئات، كل فئة لها "category" و "array" من الأذكار
        for category_item in data:
            category = category_item.get("category", "")
            array_items = category_item.get("array", [])
            
            for zekr_item in array_items:
                zekr = zekr_item.get("text", "").strip()
                if not zekr: continue
                
                adhkar_list.append({
                    "id": f"adhkar-{len(adhkar_list)+1:05d}",
                    "text": zekr,
                    "category": category,
                    "count": zekr_item.get("count", ""),
                    "reference": zekr_item.get("reference", ""), # قد لا يكون موجوداً في كل الأذكار
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
        sys.exit(1) # إجبار السكربت على إظهار الخطأ

if __name__ == "__main__":
    main()
