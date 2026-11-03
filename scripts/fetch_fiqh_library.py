import os
import json
import sys
import gzip
from pathlib import Path

try:
    from datasets import load_dataset
except ImportError:
    print("❌ مكتبة 'datasets' غير مثبتة.")
    sys.exit(1)

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "fiqh-library.json.gz"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print("⬇️ جاري تحميل قاعدة البيانات الفقهية الضخمة (IslamQA)...")
    print("⏳ قد يستغرق هذا بضع دقائق، يرجى الانتظار...")
    
    try:
        dataset = load_dataset("abdulmunimjemal/IslamQA-Multilingual-Dataset", split="train")
        print(f"✅ تم التحميل بنجاح! عدد الفتاوى الإجمالي: {len(dataset)}")
        
        fiqh_library = []
        
        for idx, item in enumerate(dataset):
            if item.get("lang") != "ar":
                continue
                
            conversations = item.get("conversations", [])
            question = ""
            answer = ""
            
            for msg in conversations:
                if msg.get("from") == "human":
                    question = msg.get("value", "").strip()
                elif msg.get("from") == "gpt":
                    answer = msg.get("value", "").strip()
            
            if not question or not answer or len(answer) < 50:
                continue
                
            fiqh_library.append({
                "id": f"fiqh-{idx+1:05d}",
                "question": question,
                "answer": answer,
                "source": "IslamQA (المنهج السلفي)",
                "url": item.get("url", ""),
                "type": "fiqh"
            })
            
        print(f"📝 تم تصفية {len(fiqh_library)} فتوى عربية صالحة للاستخدام.")
        
        print("🗜️ جاري ضغط الملف لتقليل حجمه...")
        with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
            json.dump(fiqh_library, f, ensure_ascii=False)
            
        size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
        print(f"✅ تم حفظ المكتبة الفقهية بنجاح في: {OUTPUT_FILE} (الحجم: {size_mb:.1f} ميجابايت)")
        
    except Exception as e:
        print(f"❌ حدث خطأ أثناء التحميل: {e}")

if __name__ == "__main__":
    main()
