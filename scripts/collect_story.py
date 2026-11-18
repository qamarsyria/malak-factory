"""
collect_story.py — المرحلة 1: جمع المصادر (نظام السرد الزمني العميق)
يقرأ دليل القصة (Story Guide) من stories.json، ويبني السرد القصصي بناءً عليه.
يطبق قواعد "بداية الخلق" الصارمة، ويجلب الآيات من API.
"""

import os, re, sys, json, time
from pathlib import Path
from datetime import datetime, timezone
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker, notify_telegram

# ═══════════════════════════════════════════════════════════
# المسارات
# ═══════════════════════════════════════════════════════════
STORIES_JSON       = _REPO_ROOT / "data" / "library" / "stories.json"
STORIES_OUTPUT     = _REPO_ROOT / "data" / "stories"

# ═══════════════════════════════════════════════════════════
# استدعاء LLM
# ═══════════════════════════════════════════════════════════
def call_llm(prompt: str, is_json: bool = True, timeout: int = 300, max_tokens: int = 8192):
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:5]
    if not models:
        print("❌ لا توجد موديلات متاحة!")
        return None

    for model in models:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}
                }
                if is_json:
                    body["generationConfig"]["responseMimeType"] = "application/json"
                
                r = requests.post(url, json=body, timeout=timeout)
                
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts: break
                    text = parts[0].get("text", "")
                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s: break
                        text = text[s:e+1]
                    try:
                        parsed = json.loads(text) if is_json else text
                        if hasattr(pool, '_advance'): pool._advance()
                        elif hasattr(pool, 'advance'): pool.advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError: break
                elif r.status_code == 429:
                    pool.mark_failed(3600)
                elif r.status_code in (404, 503):
                    breaker.record_failure(f"model:{model}")
                    break
            except Exception as e:
                pass
    return None

# ═══════════════════════════════════════════════════════════
# جلب الآيات من API
# ═══════════════════════════════════════════════════════════
def fetch_ayah_from_api(surah: int, ayah: int) -> dict:
    try:
        text_url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/quran-uthmani"
        tafsir_url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/ar.muyassar"
        
        text_data = ""
        r_text = requests.get(text_url, timeout=10)
        if r_text.status_code == 200:
            text_data = r_text.json()["data"]["text"]
            
        tafsir_data = ""
        r_tafsir = requests.get(tafsir_url, timeout=10)
        if r_tafsir.status_code == 200:
            tafsir_data = r_tafsir.json()["data"]["text"]
            
        return {"text": text_data, "tafsir": tafsir_data}
    except Exception:
        return {"text": "", "tafsir": ""}

# ═══════════════════════════════════════════════════════════
# بناء السرد القصصي من دليل القصة
# ═══════════════════════════════════════════════════════════
def build_narrative(story_data: dict) -> dict:
    guide = story_data.get("story_guide", {})
    title = story_data.get("title", "")
    era = story_data.get("era", "")
    
    core_idea = guide.get("core_idea", "")
    timeline = guide.get("timeline", [])
    style = guide.get("narrative_style", "")
    strict_mode = guide.get("strict_mode", False)
    special_rules = guide.get("special_rules", [])

    timeline_text = "\n".join([f"- {t}" for t in timeline])
    rules_text = "\n".join([f"- {r}" for r in special_rules])

    strict_instruction = ""
    if strict_mode:
        strict_instruction = """
⚠️ وضع الصرامة القصوى (Strict Mode):
هذه القصة من عهد "بداية الخلق". يُمنع منعاً باتاً استخدام أي إسرائيليات أو صيغ التمريض (مثل: يُروى، قيل). 
الاعتماد فقط على آيات القرآن الكريم والأحاديث النبوية الصحيحة. 
اللغة يجب أن تكون عقائدية، دقيقة، ومهيبة، تليق بمقام الألوهية.
"""
    else:
        strict_instruction = """
⚠️ تنبيه:
يمكنك استخدام التفاصيل من كتب التفسير (ابن كثير، الطبري)، ولكن أي معلومة غير قطعية يجب أن تُسبق أو تُلحق بصيغة التمريض (يُروى، قيل) وعبارة (والله أعلم).
"""

    prompt = f"""أنت مؤرخ إسلامي خبير ومتخصص في السرد القصصي التاريخي.
مهمتك كتابة سرد تاريخي زمني فائق التفصيل لقصة: "{title}".

الفكرة الأساسية للقصة:
{core_idea}

الأسلوب المطلوب:
{style}

⚠️ قاعدة الترتيب الإلزامي الصارمة جداً (خط أحمر):
يجب أن يكون عدد الأحداث في مخرجات الـ events مساوياً تماماً لعدد البنود في "التسلسل الزمني (timeline)" أدناه، وبحيث يُمثّل كل بند من هذه البنود حدثاً مستقلًا (event) وبنفس الترتيب الحرفي تماماً (البند الأول يُقابل event_01، البند الثاني يُقابل event_02، وهكذا دون أي دمج، أو تقديم، أو تأخير، أو حذف):

بنود التس
