import os
import json
import sys
from pathlib import Path

# محاولة استيراد مكتبة datasets (تحتاج تثبيت)
try:
    from datasets import load_dataset
except ImportError:
    print("❌ مكتبة 'datasets' غير مثبتة. يرجى تثبيتها عبر pip install datasets")
    sys.exit(1)

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "fiqh-library.json"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print("⬇️ جاري تحميل قاعدة البيانات الفقهية الضخمة (IslamQA - منهج السلف)...")
    print("⏳ قد يستغرق هذا بضع دقائق، يرجى الانتظار...")
    
    try:
        # تحميل مجموعة بيانات الفتاوى الإسلامية من HuggingFace
        # نستخدم مجموعة بيانات موثوقة تحتوي على أسئلة وأجوبة فقهية
        dataset = load_dataset("Omar40/IslamQA", split="train")
        
        print(f"✅ تم التحميل بنجاح! عدد الفتاوى المتاحة: {len(dataset)}")
        
        fiqh_library = []
        
        for idx, item in enumerate(dataset):
            # استخراج الحقول (تختلف أسماء الحقول حسب الداتا سيت، سنقوم بتوحيدها)
            question = item.get("question", "").strip()
            answer = item.get("answer", "").strip()
            
            if not question or not answer or len(answer) < 50:
                continue
                
            fiqh_library.append({
                "id": f"fiqh-{idx+1:05d}",
                "question": question,
                "answer": answer,
                "source": "IslamQA (المنهج السلفي)",
                "type": "fiqh"
            })
            
        print(f"📝 تم تصفية {len(fiqh_library)} فتوى صالحة للاستخدام.")
        
        # حفظ الملف
        OUTPUT_FILE.write_text(json.dumps(fiqh_library, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ تم حفظ المكتبة الفقهية بنجاح في: {OUTPUT_FILE}")
        
    except Exception as e:
        print(f"❌ حدث خطأ أثناء التحميل: {e}")

if __name__ == "__main__":
    main()
