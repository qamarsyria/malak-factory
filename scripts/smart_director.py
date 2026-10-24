import os, json, requests, re
from gemini_tts_pool import get_pool

PROMPT = """You are a documentary director for a serious Islamic history channel.
Read the Arabic text and return ONLY a valid JSON object:
1. "visual": 2-4 English words for a background image (nature, mosque, desert).
2. "sfx": 1-3 English words for a natural sound (wind, rain, birds). Leave empty if none.
3. "voice_style": A short English hint in parentheses for the narrator's tone.

CRITICAL RULES FOR VOICE_STYLE:
- It MUST be exactly ONE of these mild, professional tones:
  "(in a calm, dignified tone)"
  "(in a warm, narrative tone)"
  "(in a solemn, respectful tone)"
  "(in a gentle, reflective tone)"
- NEVER use words like: crying, tearful, trembling, pleading, raspy, whispering, or angry. The narrator is a calm, professional documentary narrator.

Return exactly like this: {"visual": "...", "sfx": "...", "voice_style": "..."}

Text: "{text}"
"""

FALLBACK_SCENES = {
    "حزن": {"visual": "rain leaves", "sfx": "light rain", "voice_style": "(in a gentle, reflective tone)"},
    "خاف": {"visual": "storm clouds", "sfx": "thunder", "voice_style": "(in a solemn, respectful tone)"},
    "مكر": {"visual": "dark forest", "sfx": "whisper", "voice_style": "(in a calm, dignified tone)"},
    "معركة": {"visual": "desert dunes", "sfx": "sword clash", "voice_style": "(in a solemn, respectful tone)"},
    "دعاء": {"visual": "starry night", "sfx": "breeze", "voice_style": "(in a calm, dignified tone)"},
}
DEFAULT_SCENE = {"visual": "islamic mosque", "sfx": "", "voice_style": "(in a calm, dignified tone)"}

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
                # تأكد من أن التلميح لا يحتوي على كلمات مبالغ فيها
                if any(w in parsed["voice_style"].lower() for w in ["cry", "tear", "trembl", "plead", "rasp", "whisper"]):
                    parsed["voice_style"] = DEFAULT_SCENE["voice_style"]
                return parsed
    except Exception:
        pass
    for keyword, scene in FALLBACK_SCENES.items():
        if keyword in text: return scene
    return DEFAULT_SCENE
