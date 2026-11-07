import os
import json
import sys
import gzip
from pathlib import Path
from datasets import load_dataset

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "fiqh-library.json.gz"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("⬇️ جاري تحميل مكتبة الفقه (IslamQA.info) من Hugging Face...")
    
    try:
        # تحميل مجموعة البيانات
        dataset = load_dataset("abdulmunimjemal/IslamQA-Multilingual-Dataset", split="train")
        
        fiqh_list = []
        
        for item in dataset:
            # نأخذ فقط اللغة العربية
            if item.get("lang") != "ar":
                continue
                
            conversations = item.get("conversations", [])
            if not conversations:
                continue
                
            # استخراج السؤال والجواب من المحادثة
            question = ""
            answer = ""
            
            for msg in conversations:
                if msg.get("from") == "human":
                    question = msg.get("value", "").strip()
                elif msg.get("from") == "gpt":
                    answer = msg.get("value", "").strip()
            
            if not question or not answer:
                continue
                
            # تنظيف النص من أي وسوم HTML
            import re
            question = re.sub(r'<[^>]+>', '', question)
            answer = re.sub(r'<[^>]+>', '', answer)
            
            # إزالة المقدمات الطويلة (اختياري)
            if len(answer) < 100 or len(answer) > 5000:
                continue
                
            fiqh_list.append({
                "id": f"fiqh-{len(fiqh_list)+1:05d}",
                "question": question,
                "answer": answer,
                "source": "IslamQA.info",
                "url": item.get("url", ""),
                "type": "fiqh"
            })
        
        print(f"📝 تم تجميع {len(fiqh_list)} سؤال وجواب فقهي.")
        
        if len(fiqh_list) == 0:
            print("❌ لم يتم العثور على أي بيانات عربية.")
            sys.exit(1)
            
        print("🗜️ جاري ضغط الملف...")
        with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
            json.dump(fiqh_list, f, ensure_ascii=False)
            
        size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
        print(f"✅ تم حفظ مكتبة الفقه بنجاح في: {OUTPUT_FILE} (الحجم: {size_mb:.1f} ميجابايت)")
        
    except Exception as e:
        print(f"❌ حدث خطأ: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
