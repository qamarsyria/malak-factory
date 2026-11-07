import os
import sys
import json
import random
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "whispers" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_whisper.txt"

# 🌟 استيراد البنك الجديد
from whispers_bank import WHISPERS_BANK

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # اختيار النوع (رومانسي / دعاء / شعر) عشوائياً
    category_key = random.choice(list(WHISPERS_BANK.keys()))
    text = random.choice(WHISPERS_BANK[category_key])
    
    # تحديد العنوان والنوع (عشان الفيديو والصوت)
    if "romantic" in category_key:
        content_title = "همسات ملك - خاطرة"
        category_type = "romantic"
    elif "dua" in category_key:
        content_title = "همسات ملك - دعاء"
        category_type = "dua"
    else:
        content_title = "همسات ملك - شعر"
        category_type = "romantic"
    
    segments = []
    segments.append({
        "type": "narration",
        "text": text,
        "visual": "rose garden butterflies" if category_type == "romantic" else "islamic architecture mosque sunset"
    })
    
    ep_data = {
        "episode_number": 1,
        "story_id": "whispers",
        "title": content_title,
        "category": category_type, # 🌟 مهم جداً عشان الصوت
        "segments": segments
    }
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"whisper-{timestamp}.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم إنشاء همسة: {output_path}", flush=True)

if __name__ == "__main__":
    main()
