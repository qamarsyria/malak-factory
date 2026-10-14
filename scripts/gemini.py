"""إعادة صياغة النص إلى سكريبت فيديو عبر Gemini."""
import json
import re
import requests
from config import GEMINI_API_KEY

MODELS_TO_TRY = [
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]

PROMPT_TEMPLATE = """أنت كاتب محتوى دعوي محترف.

المهمة: حوّل النص التالي إلى سكريبت فيديو دعوي قصير مدته 40-50 ثانية.

قواعد صارمة:
1. لا تُضف أي معلومة غير موجودة في النص الأصلي.
2. لا تذكر مذاهب فقهية ولا فرق إسلامية.
3. لا تذكر أسماء علماء معاصرين.
4. الصياغة: سؤال استفهامي مشوّق (8-12 كلمة) ثم جواب مباشر (30-40 كلمة).
5. الأسلوب: فصحى مبسّطة، دافئة، مؤثرة.
6. احفظ الآيات والأحاديث كما هي حرفياً إن وُجدت.

النص الأصلي:
{source_text}

المصدر (للاستئناف فقط):
{attribution}

أعد النتيجة بصيغة JSON فقط:
{{
  "question": "...",
  "answer": "..."
}}"""


def _call_gemini(model, prompt):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "topP": 0.9,
            "maxOutputTokens": 500,
            "responseMimeType": "application/json",
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
    }
    try:
        r = requests.post(url, json=body, timeout=30)
        if r.status_code != 200:
            print(f"⚠️  {model}: HTTP {r.status_code} — {r.text[:200]}")
            return None
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            print(f"⚠️  {model}: لا يوجد candidates")
            return None
        parts = (cands[0].get("content") or {}).get("parts") or []
        if not parts:
            print(f"⚠️  {model}: لا يوجد parts")
            return None
        text = parts[0].get("text", "").strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"⚠️  {model}: JSON غير صالح — {e}")
        return None
    except Exception as e:
        print(f"⚠️  {model}: {e}")
        return None


def reformulate(item):
    """يرجع dict بصيغة {question, answer, used_original}."""
    if not GEMINI_API_KEY:
        print("⚠️  GEMINI_API_KEY غير موجود — استخدام النص الأصلي")
        return _fallback(item)

    prompt = PROMPT_TEMPLATE.format(
        source_text=item["text"],
        attribution=item.get("attribution", ""),
    )

    for model in MODELS_TO_TRY:
        print(f"🤖 محاولة {model}...")
        result = _call_gemini(model, prompt)
        if result and result.get("question") and result.get("answer"):
            q = str(result["question"]).strip()
            a = str(result["answer"]).strip()
            if 5 <= len(q.split()) <= 25 and 20 <= len(a.split()) <= 80:
                print(f"✅ نجح {model}")
                return {"question": q, "answer": a, "used_original": False}
            print(f"⚠️  {model}: النص خارج النطاق — رفض")

    print("⚠️  كل النماذج فشلت — استخدام النص الأصلي")
    return _fallback(item)


def _clean_hadith_start(text):
    """يزيل البدايات الشائعة من نصوص الأحاديث."""
    patterns = [
        r'^قَالَ\s*:\s*',
        r'^قَالَتْ\s*:\s*',
        r'^قَالَ\s+',
        r'^قَالَتْ\s+',
        r'^عَنْ\s+[^:]+?:\s*',
        r'^عَنِ\s+[^:]+?:\s*',
        r'^رَسُولُ\s+اللَّهِ.*?:\s*',
    ]
    for p in patterns:
        text = re.sub(p, '', text, count=1).strip()
    return text


def _fallback(item):
    """يرجع النص الأصلي كـ سؤال/جواب."""
    text = item["text"].strip()
    source_type = item.get("source_type", "")

    if source_type in ("riyad", "hadith"):
        question = ""
    elif source_type == "ruqyah":
        question = item.get("question", "")
    else:
        q = item.get("question", "").strip()
        question = q if q else ""

    return {"question": question, "answer": text, "used_original": True}
