import os
import re
import sys
import json
import gzip
import random
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUTPUT_DIR = _REPO_ROOT / "data" / "daily" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"
STATE_FILE = _REPO_ROOT / "state" / "daily_state.json"
LIBRARIES_DIR = _REPO_ROOT / "data" / "libraries"

DREAMY_VISUALS = [
    "enchanted forest magical", "rose garden butterflies", "dreamy fantasy landscape",
    "starry night sky milky way", "waterfall magical nature", "cherry blossom spring",
    "islamic architecture mosque sunset"
]

# 🌟 تحميل مكتبة الآيات المحلية (مثل القصص)
_ayah_lib = []
try:
    lib_path = LIBRARIES_DIR / "ayah-library.json.gz"
    if lib_path.exists():
        with gzip.open(lib_path, "rt", encoding="utf-8") as f:
            _ayah_lib = json.load(f)
        print(f"✅ تم تحميل {len(_ayah_lib)} آية من الملف المحلي لليوميات.")
except Exception as e:
    print(f"⚠️ فشل تحميل مكتبة الآيات: {e}")

def load_library(filename):
    path = LIBRARIES_DIR / filename
    if not path.exists(): return []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text)).strip()

def pick_unused(items, text_key, used_ids, min_len=50, max_len=1500):
    valid_items = [i for i in items if min_len <= len(i.get(text_key, "")) <= max_len]
    unused_items = [i for i in valid_items if i.get("id") not in used_ids]
    
    if not unused_items:
        print(f"♻️ تم استهلاك جميع العناصر، إعادة تصفير الذاكرة...")
        unused_items = valid_items
        used_ids = []
        
    if not unused_items: return None, used_ids
    
    picked = random.choice(unused_items)
    used_ids.append(picked["id"])
    return picked, used_ids

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    state = {"run_count": 0, "used_ids": {"hadith": [], "fiqh": [], "quran": [], "adhkar": []}}
    if STATE_FILE.exists():
        try: 
            saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            state.update(saved)
            if "used_ids" not in state: state["used_ids"] = {"hadith": [], "fiqh": [], "quran": [], "adhkar": []}
        except: pass
    
    content_type = state["run_count"] % 4
    type_names = ["hadith", "fiqh", "quran", "adhkar"]
    current_type_name = type_names[content_type]
    used_list = state["used_ids"].get(current_type_name, [])
    
    state["run_count"] += 1
    print(f"🔄 التشغيل: {state['run_count']} | النوع: {current_type_name} | المستخدم سابقاً: {len(used_list)}", flush=True)

    segments = []
    content_title = ""
    visual = random.choice(DREAMY_VISUALS)

    if content_type == 0:
        lib = load_library("hadith-library.json.gz")
        item, used_list = pick_unused(lib, "text", used_list, 100, 1500)
        if not item: sys.exit(1)
        content_title = f"حديث: {item.get('chapter', 'من صحيح البخاري ومسلم')}"
        segments.append({"type": "narration", "text": "في هذا المقطع، نتوقف مع حديث نبوي شريف، يضيء لنا الطريق.", "visual": visual})
        segments.append({"type": "hadith", "text": clean_text(item["text"]), "display": item.get("book", "صحيح"), "visual": visual})
        segments.append({"type": "narration", "text": f"رواه {item.get('book', '')}، في {item.get('chapter', '')}.", "visual": visual})

    elif content_type == 1:
        lib = load_library("fiqh-library.json.gz")
        item, used_list = pick_unused(lib, "answer", used_list, 200, 1500)
        if not item: sys.exit(1)
        content_title = "فائدة فقهية"
        segments.append({"type": "narration", "text": "كثيراً ما يبحث المسلم عن إجابة شافية، في مسألة فقهية.", "visual": visual})
        segments.append({"type": "narration", "text": clean_text(item["question"]), "visual": visual})
        segments.append({"type": "fiqh", "text": clean_text(item["answer"]), "display": "منهج السلف", "visual": visual})

    elif content_type == 2:
        # 🌟 3. آية وحكاية (من الملف المحلي وبشكل مصحح)
        if not _ayah_lib:
            print("❌ مكتبة الآيات المحلية فارغة!")
            sys.exit(1)
            
        # نختار آية تحتوي على تفسير
        valid_ayat = [i for i in _ayah_lib if i.get("tafsir") and len(i.get("tafsir")) > 50]
        if not valid_ayat:
            valid_ayat = _ayah_lib
            
        item, used_list = pick_unused(valid_ayat, "tafsir", used_list, 50, 1500)
        if not item: sys.exit(1)
        
        surah_name = item["surah_name"].replace("سورة ", "").replace("سُورَةُ ", "").strip()
        surah_num = item["surah_number"]
        ayah_num = item["ayah_number"]
        
        content_title = f"آية وحكاية: سورة {surah_name}"
        
        segments.append({"type": "narration", "text": f"في سورة {surah_name}، يقول الله تعالى:", "visual": visual})
        # 🌟 تمرير أرقام السورة والآية لكي يتعرف عليها محرك الصوت ويقرأها
        segments.append({"type": "quran", "surah": surah_num, "surah_name": surah_name, "ayah_numbers": [ayah_num], "display": f"{surah_name}: {ayah_num}", "visual": visual})
        segments.append({"type": "narration", "text": f"يقول التفسير الميسر: {clean_text(item['tafsir'])}", "visual": visual})

    else:
        lib = load_library("adhkar-library.json.gz")
        item, used_list = pick_unused(lib, "text", used_list, 20, 800)
        if not item: sys.exit(1)
        content_title = f"ذكر: {item.get('category', 'من حصن المسلم')}"
        segments.append({"type": "narration", "text": "من أذكار حصن المسلم، التي تطمئن بها القلوب.", "visual": visual})
        segments.append({"type": "adhkar", "text": clean_text(item["text"]), "display": item.get("reference", ""), "visual": visual})

    segments.append({"type": "cta", "text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». شارك المقطع لتعم الفائدة. ملاك المعرفة.", "visual": "mosque architecture sunset"})

    ep_data = {"episode_number": 1, "story_id": "daily", "title": content_title, "segments": segments}
    
    state["used_ids"][current_type_name] = used_list
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"daily-{timestamp}.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم إنشاء المقطع: {output_path}", flush=True)

if __name__ == "__main__":
    main()
