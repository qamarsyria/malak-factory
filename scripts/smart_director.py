import os, json, requests, re
from gemini_tts_pool import get_pool

PROMPT = """You are a movie scene director for an Islamic documentary.
Read the Arabic text and return ONLY a valid JSON object with these keys:
1. "visual": 2-4 English words for a background image.
2. "sfx": 1-3 English words for a sound effect. Leave empty if none.
3. "voice_style": A short English hint in parentheses for the tone.

RULES: NO humans, NO music. Return exactly like this: {"visual": "...", "sfx": "...", "voice_style": "..."}

Text: "{text}"
"""

FALLBACK_SCENES = {
    "حزن": {"visual": "rain leaves", "sfx": "light rain", "voice_style": "(in a sad tone)"},
    "خاف": {"visual": "storm clouds", "sfx": "thunder", "voice_style": "(in a fearful tone)"},
    "مكر": {"visual": "dark forest", "sfx": "whisper", "voice_style": "(in a secretive tone)"},
    "معركة": {"visual": "desert dunes", "sfx": "sword clash", "voice_style": "(in an urgent tone)"},
    "دعاء": {"visual": "starry night", "sfx": "breeze", "voice_style": "(in a humble tone)"},
}
DEFAULT_SCENE = {"visual": "islamic mosque", "sfx": "", "voice_style": "(in a calm tone)"}

def direct_scene(text: str) -> dict:
    if not text.strip(): return DEFAULT_SCENE
    try:
        pool = get_pool()
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        body = {"contents": [{"parts": [{"text": PROMPT.format(text=text[:800])}]}], "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
        r = requests.post(url, json=body, timeout=20)
        if r.status_code == 200:
            parsed = json.loads(r.json()['candidates'][0]['content']['parts'][0]['text'])
            if isinstance(parsed, dict) and "visual" in parsed and "voice_style" in parsed:
                return parsed
    except Exception:
        pass
    for keyword, scene in FALLBACK_SCENES.items():
        if keyword in text: return scene
    return DEFAULT_SCENE
