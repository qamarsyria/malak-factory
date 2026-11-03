import os
import json
import sys
import gzip
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "ayah-library.json.gz"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("⬇️ جاري تحميل القرآن الكريم كاملاً + التفسير الميسر...")
    
    try:
        print("📖 جاري تحميل النص العثماني...")
        url_quran = "https://api.alquran.cloud/v1/quran/quran-uthmani"
        r_quran = requests.get(url_quran, timeout=30)
        if r_quran.status_code != 200:
            print(f"❌ فشل تحميل النص: HTTP {r_quran.status_code}")
            sys.exit(1)
        quran_data = r_quran.json()["data"]["surahs"]
        
        print("📚 جاري تحميل التفسير الميسر...")
        url_tafsir = "https://api.alquran.cloud/v1/quran/ar.muyassar"
        r_tafsir = requests.get(url_tafsir, timeout=30)
        if r_tafsir.status_code != 200:
            print(f"❌ فشل تحميل التفسير: HTTP {r_tafsir.status_code}")
            sys.exit(1)
        tafsir_data = r_tafsir.json()["data"]["surahs"]
        
        print("🔗 جاري دمج الآيات مع التفسير...")
        ayah_library = []
        
        # دمج السور والآيات
        for surah_idx, surah in enumerate(quran_data):
            surah_name = surah["name"]
            surah_number = surah["number"]
            tafsir_surah = tafsir_data[surah_idx]
            
            for ayah_idx, ayah in enumerate(surah["ayahs"]):
                ayah_number = ayah["numberInSurah"]
                text = ayah["text"]
                tafsir = tafsir_surah["ayahs"][ayah_idx]["text"]
                
                ayah_library.append({
                    "id": f"ayah-{surah_number:03d}-{ayah_number:03d}",
                    "surah_number": surah_number,
                    "surah_name": surah_name,
                    "ayah_number": ayah_number,
                    "text": text,
                    "tafsir": tafsir,
                    "type": "quran"
                })
                
        print(f"📝 تم تجميع {len(ayah_library)} آية مع تفسيرها.")
        
        print("🗜️ جاري ضغط الملف...")
        with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
            json.dump(ayah_library, f, ensure_ascii=False)
            
        size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
        print(f"✅ تم حفظ مكتبة الآيات بنجاح في: {OUTPUT_FILE} (الحجم: {size_mb:.1f} ميجابايت)")
        
    except Exception as e:
        print(f"❌ حدث خطأ: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
