import os
import json
import sys
import gzip
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "adhkar-library.json.gz"

# قائمة بالروابط المحتملة (سيحاول السكربت واحداً تلو الآخر)
URLS = [
    "https://raw.githubusercontent.com/rn0x/Adhkar-json/main/azkar.json",
    "https://raw.githubusercontent.com/rn0x/Adhkar-json/main/adhkar.json",
    "https://raw.githubusercontent.com/osamayy/azkar-db/main/azkar.json",
    "https://raw.githubusercontent.com/osamayy/azkar-db/master/azkar.json"
]

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("⬇️ جاري تحميل مكتبة الأذكار والأدعية...")
    
    data = None
    for url in URLS:
        try:
            print(f"   محاولة جلب من: {url}")
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                print(f"   ✅ نجح التحميل من هذا الرابط!")
                break
            else:
                print(f"   ⚠️ فشل (HTTP {r.status_code})")
        except Exception as e:
            print(f"   ⚠️ خطأ: {e}")
            
    if not data:
        print("❌ فشل التحميل من جميع الروابط المتاحة.")
        sys.exit(1)
        
    adhkar_list = []
    
    # التعامل مع بنية البيانات (سواء كانت مصفوفة أقسام أو مصفوفة أذكار مباشرة)
    if isinstance(data, list):
        for item in data:
            # النمط الأول: عناصر مباشرة
            if "zekr" in item:
                zekr_text = item.get("zekr", "").strip()
                category = item.get("category", "")
                count = item.get("count", "")
                reference = item.get("reference", "")
            # النمط الثاني: أقسام بها مصفوفة "array"
            elif "array" in item:
                category = item.get("category", "")
                for sub_item in item.get("array", []):
                    zekr_text = sub_item.get("text", "").strip()
                    if zekr_text:
                        adhkar_list.append({
                            "id": f"adhkar-{len(adhkar_list)+1:05d}",
                            "text": zekr_text,
                            "category": category,
                            "count": sub_item.get("count", ""),
                            "reference": sub_item.get("reference", ""),
                            "type": "adhkar"
                        })
                continue
            else:
                continue
                
            if zekr_text:
                adhkar_list.append({
                    "id": f"adhkar-{len(adhkar_list)+1:05d}",
                    "text": zekr_text,
                    "category": category,
                    "count": count,
                    "reference": reference,
                    "type": "adhkar"
                })
                
    print(f"📝 تم تجميع {len(adhkar_list)} ذكر/دعاء.")
    
    if len(adhkar_list) == 0:
        print("❌ لم يتم العثور على أذكار صالحة في الملف.")
        sys.exit(1)
        
    print("🗜️ جاري ضغط الملف...")
    with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
        json.dump(adhkar_list, f, ensure_ascii=False)
        
    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"✅ تم حفظ مكتبة الأذكار بنجاح في: {OUTPUT_FILE} (الحجم: {size_mb:.1f} ميجابايت)")

if __name__ == "__main__":
    main()
