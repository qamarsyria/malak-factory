"""
smart_director.py — المخرج الذكي لاكتشاف المشاعر والصور
- يستخدم Gemini Flash لتحليل النص.
- يعيد: visual (صورة)، sfx (مؤثر)، voice_style (نبرة الصوت).
- يحتوي على "قاموس احتياطي" في حال فشل Gemini.
"""

import os
import json
import requests
import re
from gemini_tts_pool import get_pool

PROMPT = """You are a movie scene director for an Islamic documentary.
Read the Arabic text and return ONLY a valid JSON object with these keys:
1. "visual": 2-4 English words for a background image.
2. "sfx": 1-3 English words for a sound effect (wind, rain, thunder, birds). Leave empty if none.
3. "voice_style": A short English hint in parentheses for the narrator's tone (e.g., "(in a sad, tearful tone)", "(in a fearful, trembling tone)", "(in a calm, dignified tone)").

ABSOLUTE RULES:
- NO humans, NO faces, NO prophets, NO angels. Only nature/architecture.
- NO music. Only natural sounds.
- Return exactly like this: {"visual": "...", "sfx": "...", "voice_style": "..."}

Text: "{text}"
"""

# قاموس احتياطي (في حال فشل Gemini)
FALLBACK_SCENES = {
    "حزن": {"visual": "rain autumn leaves", "sfx": "light rain", "voice_style": "(in a sad, tearful tone)"},
    "خاف": {"visual": "storm sky dark clouds", "sfx": "thunder distant", "voice_style": "(in a fearful, trembling tone)"},
    "مكر": {"visual": "dark forest fog", "sfx": "whisper quiet", "voice_style": "(in a secretive, whispering tone)"},
    "معركة": {"visual": "desert sand dunes", "sfx": "sword clash", "voice_style": "(in a strong, urgent tone)"},
    "فرح": {"visual": "peaceful nature sunrise", "sfx": "birds chirping", "voice_style": "(in a joyful, grateful tone)"},
    "دعاء": {"visual": "starry night sky desert", "sfx": "gentle breeze", "voice_style": "(in a humble, pleading tone)"},
}

DEFAULT_SCENE = {"visual": "islamic architecture mosque", "sfx": "", "voice_style": "(in a calm, dignified tone)"}

def direct_scene(text: str) -> dict:
    if not text.strip():
        return DEFAULT_SCENE

    # 1. محاولة استخدام Gemini Flash
    try:
        pool = get_pool()
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": PROMPT.format(text=text[:800])}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
        }
        r = requests.post(url, json=body, timeout=20)
        if r.status_code == 200:
            data = r.json()
            txt = data['candidates'][0]['content']['parts'][0]['text']
            parsed = json.loads(txt)
            if "visual" in parsed and "voice_style" in parsed:
                print(f"   🎬 المخرج (AI): {parsed['voice_style']}")
                return parsed
    except Exception as e:
        print(f"   ⚠️ المخرج (AI) فشل: {e}")

    # 2. القاموس الاحتياطي (إذا فشل Gemini)
    for keyword, scene in FALLBACK_SCENES.items():
        if keyword in text:
            print(f"   🎬 المخرج (قاموس): {scene['voice_style']}")
            return scene

    print("   🎬 المخرج (افتراضي): نبرة هادئة")
    return DEFAULT_SCENE

if __name__ == "__main__":
    # اختبار سريع
    print(direct_scene("فخرج منها خائفاً يترقب"))
