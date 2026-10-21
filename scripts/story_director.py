"""المخرج السينمائي: يحلل السرد ويختار الصور والأصوات المناسبة لكل جملة."""
import json
import requests
from gemini_tts_pool import get_pool
from config import GEMINI_MODELS_TO_TRY

PROMPT = """You are an expert cinematic director for an Islamic documentary.
I will give you a sentence from a story. You must choose a background image search query (for Pixabay) and a sound effect search query (for Freesound) that perfectly match the mood and setting of the sentence.

Rules:
1. NO humans, NO prophets, NO animals with faces (unless requested), NO idols. Only nature, architecture, or abstract concepts.
2. The visual query must be in English, short (2-4 words).
3. The SFX query must be in English, short (1-3 words), from nature (wind, rain, birds, water, fire, crickets, etc.). NO MUSIC.
4. Return ONLY a valid JSON object: {"visual": "query here", "sfx": "query here"}

Sentence: "{text}"
JSON:"""

def direct_scene(text: str) -> dict:
    if not text.strip():
        return {"visual": "islamic nature", "sfx": "wind"}
    
    try:
        pool = get_pool()
    except Exception:
        return {"visual": "islamic nature", "sfx": "wind"}

    for model in GEMINI_MODELS_TO_TRY:
        try:
            key = pool.get_current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {
                "contents": [{"parts": [{"text": PROMPT.format(text=text[:400])}]}],
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
            
            visual = parsed.get("visual", "islamic nature")
            sfx = parsed.get("sfx", "wind")
            print(f"      🎬 مخرج: صورة='{visual}', صوت='{sfx}'")
            return {"visual": visual, "sfx": sfx}
        except Exception:
            continue
            
    return {"visual": "islamic nature", "sfx": "wind"}
