"""كاشف المشاهد — يستخدم Gemini Flash لتحليل النص وتحديد المشهد والمؤثرات."""
import json
import requests
import re
from gemini_tts_pool import get_pool
from config import GEMINI_MODELS_TO_TRY

PROMPT = """You are a movie scene director. Read the Arabic text and return ONLY a JSON object.
Choose the most fitting scene from this list: "conspiracy", "fear", "sadness", "calm", "battle", "royal", "night", "sea".

Format: {"scene": "conspiracy"}

Text:
"{text}"

JSON:"""

def detect_scene(text: str) -> str:
    if not text.strip():
        return "calm"
    
    try:
        pool = get_pool()
    except Exception:
        return "calm"

    for model in GEMINI_MODELS_TO_TRY:
        try:
            key = pool.get_current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {
                "contents": [{"parts": [{"text": PROMPT.format(text=text[:800])}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json"
                },
            }
            r = requests.post(url, json=body, timeout=20)
            if r.status_code == 429:
                continue
            if r.status_code != 200:
                continue
            
            data = r.json()
            cands = data.get("candidates") or []
            if not cands: continue
            
            parts = (cands[0].get("content") or {}).get("parts") or []
            if not parts: continue
            
            result_text = parts[0].get("text", "")
            parsed = json.loads(result_text)
            scene = parsed.get("scene", "calm")
            
            # التحقق من صحة المشهد
            valid_scenes = ["conspiracy", "fear", "sadness", "calm", "battle", "royal", "night", "sea"]
            if scene in valid_scenes:
                print(f"   🎬 مشهد مكتشف: {scene}")
                return scene
            return "calm"
        except Exception:
            continue
            
    return "calm"
