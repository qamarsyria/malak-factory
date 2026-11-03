import os
import re
import sys
import json
import gzip
import random
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUTPUT_DIR = _REPO_ROOT / "data" / "daily" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"
STATE_FILE = _REPO_ROOT / "state" / "daily_state.json"
LIBRARIES_DIR = _REPO_ROOT / "data" / "libraries"

# كلمات بحث الخلفيات الحالمة (Dreamy Visuals)
DREAMY_VISUALS = [
    "enchanted forest magical",
    "rose garden butterflies",
    "dreamy fantasy landscape",
    "starry night sky milky way",
    "waterfall magical nature",
    "cherry blossom spring",
    "islamic architecture mosque sunset"
]

def load_library(filename):
    """تحميل المكتبة المضغوطة بأمان"""
    path = LIBRARIES_DIR / filename
    if not path.exists():
        print(f"⚠️ المكتبة غير موجودة: {path}")
        return []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ خطأ في قراءة {filename}: {e}")
        return []

def clean_text(text):
    """تنظيف النص من أي رموز HTML أو علامات غير مرغوبة"""
    if not text: return ""
    text = re.sub(r'<[^>]+>', '', text) # إزالة وسوم HTML
    text = re.sub(r'\s+', ' ', text)    # إزالة المسافات الزائدة
    return text.strip()

def pick_random_valid(items, text_keys, min_len=100, max_len=1200):
    """اختيار عنصر عشوائي مع ضمان أن يكون طوله مناسباً لفيديو قصير"""
    valid_items = []
    for item in items:
        text = ""
        for key in text_keys:
            if key in item:
                text = item[key]
                break
        if text and min_len <= len(text) <= max_len:
            valid_items.append(item)
            
    if valid_items:
        return random.choice(valid_items)
    
    # إذا لم نجد عنصراً في النطاق المطلوب، نأخذ أي عنصر عشوائي
    return random.choice(items) if items else None

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # 🌟 عداد التشغيل (لدوران المحتوى)
    state = {"run_count": 0}
    if STATE_FILE.exists():
        try: state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except: pass
    
    content_type = state["run_count"] % 4  # 0: حديث, 1: فقه, 2: آية, 3: أذكار
    state["run_count"] += 1
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    
    print(f"🔄 رقم التشغيل: {state['run_count']} | نوع المحتوى المختار: {content_type}", flush=True)

    segments = []
    content_title = ""

    if content_type == 0:
        # 📚 1. الحديث الشريف
        lib = load_library("hadith-library.json.gz")
        item = pick_random_valid(lib, ["text"], 100, 1500)
        if not item:
            print("❌ مكتبة الحديث فارغة!"); sys.exit(1)
        
        content_title = f"حديث: {item.get('chapter', 'من صحيح البخاري ومسلم')}"
        visual = random.choice(DREAMY_VISUALS)
        
        segments.append({"type": "narration", "text": "في هذا المقطع، نتوقف مع حديث نبوي شريف، يضيء لنا الطريق.", "visual": visual})
        segments.append({"type": "hadith", "text": clean_text(item["text"]), "display": item.get("book", "صحيح"), "visual": visual})
        segments.append({"type": "narration", "text": f"رواه {item.get('book', '')}، في {item.get('chapter', '')}.", "visual": visual})

    elif content_type == 1:
        # ⚖️ 2. الفقه
        lib = load_library("fiqh-library.json.gz")
        item = pick_random_valid(lib, ["answer"], 200, 1500)
        if not item:
            print("❌ مكتبة الفقه فارغة!"); sys.exit(1)
        
        content_title = "فائدة فقهية"
        visual = random.choice(DREAMY_VISUALS)
        
        segments.append({"type": "narration", "text": "كثيراً ما يبحث المسلم عن إجابة شافية، في مسألة فقهية.", "visual": visual})
        segments.append({"type": "narration", "text": clean_text(item["question"]), "visual": visual})
        segments.append({"type": "fiqh", "text": clean_text(item["answer"]), "display": "منهج السلف", "visual": visual})

    elif content_type == 2:
        # 📖 3. آية وحكاية
        lib = load_library("ayah-library.json.gz")
        item = pick_random_valid(lib, ["tafsir"], 200, 1200)
        if not item:
            print("❌ مكتبة الآيات فارغة!"); sys.exit(1)
        
        content_title = f"آية وحكاية: {item['surah_name']}"
        visual = random.choice(DREAMY_VISUALS)
        
        segments.append({"type": "narration", "text": f"في سورة {item['surah_name']}، يقول الله تعالى:", "visual": visual})
        segments.append({"type": "quran", "text": clean_text(item["text"]), "display": f"{item['surah_name']}: {item['ayah_number']}", "visual": visual})
        segments.append({"type": "narration", "text": f"يقول التفسير الميسر: {clean_text(item['tafsir'])}", "visual": visual})

    else:
        # 📿 4. الأذكار
        lib = load_library("adhkar-library.json.gz")
        item = pick_random_valid(lib, ["text"], 20, 800)
        if not item:
            print("❌ مكتبة الأذكار فارغة!"); sys.exit(1)
        
        content_title = f"ذكر: {item.get('category', 'من حصن المسلم')}"
        visual = random.choice(DREAMY_VISUALS)
        
        segments.append({"type": "narration", "text": "من أذكار حصن المسلم، التي تطمئن بها القلوب.", "visual": visual})
        segments.append({"type": "adhkar", "text": clean_text(item["text"]), "display": item.get("reference", ""), "visual": visual})

    # إضافة الخاتمة والدعوة للمشاركة (بدون موسيقى)
    segments.append({
        "type": "cta", 
        "text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». شارك المقطع لتعم الفائدة. ملاك المعرفة.", 
        "visual": "mosque architecture sunset"
    })

    # بناء ملف JSON النهائي
    ep_data = {
        "episode_number": 1, 
        "story_id": "daily", 
        "title": content_title, 
        "segments": segments
    }
    
    output_path = OUTPUT_DIR / "daily-latest.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم إنشاء المقطع اليومي من المكتبة الموثقة: {output_path}", flush=True)

if __name__ == "__main__":
    main()
