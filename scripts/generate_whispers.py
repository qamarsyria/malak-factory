import os, sys, json, random
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "whispers" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_whisper.txt"

from whispers_bank import WHISPERS_BANK

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    category_key = random.choice(list(WHISPERS_BANK.keys()))
    item = random.choice(WHISPERS_BANK[category_key])
    text = item["text"]
    name = item.get("name", "")
    
    if "romantic" in category_key or "poetry" in category_key:
        content_title = "همسات ملك - خاطرة" if "romantic" in category_key else "همسات ملك - شعر"
        category_type = "romantic"
    else:
        content_title = "همسات ملك - دعاء"
        category_type = "dua"
    
    segments = [{"type": "narration", "text": text, "visual": ""}]
    
    ep_data = {
        "episode_number": 1,
        "story_id": "whispers",
        "title": content_title,
        "category": category_type,
        "target_name": name, # 🌟 اسم الاسم عشان يظهر في الفيديو
        "segments": segments
    }
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"whisper-{timestamp}.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ همسة: {content_title} | الاسم: {name or 'عام'}")

if __name__ == "__main__":
    main()
