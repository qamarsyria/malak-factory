ح"""إعادة صياغة النص إلى سكريبت فيديو (40-50 كلمة) عبر Gemini."""
import json
import requests
from config import GEMINI_API_KEY

# ترتيب النماذج حسب الأولوية (2026)
# gemini-flash-lite-latest هو الأكثر استقراراً، ثم نجرّب الباقي
MODELS_TO_TRY = [
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]

PROMPT_TEMPLATE = """أنت كاتب محتوى دعوي محترف.

المهمة: حوّل النص التالي إلى سكريبت فيديو دعوي قصير مدته 40-50 ثانية.

قواعد صارمة:
1. لا تُضف أي معلومة غير موجودة في النص الأصلي.
2. لا تذكر مذاهب فقهية (حنفي، مالكي، شافعي، حنبلي) ولا فرق إسلامية.
3. لا تذكر أسماء علماء معاصرين.
4. الصياغة: سؤال استفهامي مشوّق (8-12 كلمة) ثم جواب مباشر (30-40 كلمة).
5. الأسلوب: فصحى مبسّطة، دافئة، مؤثرة، دون مبالغة.
6. احفظ الآيات والأحاديث كما هي حرفياً إن وُجدت.

النص الأصلي:
{source_text}

المصدر (للاستئناف فقط، لا يُذكر في الإخراج):
{attribution}

أعد النتيجة بصيغة JSON فقط بالشكل:
{{
  "question": "...",
  "answer": "..."
}}"""


def _call_gemini(model, prompt):
    """طلب واحد إلى Gemini. يُرجع dict أو None."""
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
        # نظّف code fences إن وُجدت
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
    """
    يُعيد dict بصيغة:
      {question, answer, used_original: bool}
    إذا فشل Gemini، يُرجع النص الأصلي مباشرة.
    """
    if not GEMINI_API_KEY:
        print("⚠️  GEMINI_API_KEY غير موجود — استخدام النص الأصلي")
        return _fallback(item)

    prompt = PROMPT_TEMPLATE.format(
        source_text=item["text"],
        attribution=item.get("attribution", ""),
    )

    # جرّب النماذج بالترتيب
    for model in MODELS_TO_TRY:
        print(f"🤖 محاولة {model}...")
        result = _call_gemini(model, prompt)
        if result and result.get("question") and result.get("answer"):
            q = str(result["question"]).strip()
            a = str(result["answer"]).strip()
            # تحقق من طول السؤال والجواب
            if 5 <= len(q.split()) <= 25 and 20 <= len(a.split()) <= 80:
                print(f"✅ نجح {model}")
                return {"question": q, "answer": a, "used_original": False}
            print(f"⚠️  {model}: النص خارج النطاق — رفض")

    print("⚠️  كل النماذج فشلت — استخدام النص الأصلي")
    return _fallback(item)


def _fallback(item):
    """يرجع النص الأصلي كـ سؤال/جواب."""
    q = item.get("question", "").strip()
    text = item["text"].strip()
    if not q:
        # اشتق سؤالاً من أول جملة
        first = text.split(".")[0].strip()
        q = first[:60] + ("..." if len(first) > 60 else "")
    return {"question": q, "answer": text, "used_original": True}
