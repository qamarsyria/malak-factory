import gzip
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUTPUT_DIR = _REPO_ROOT / "data" / "daily" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"
STATE_FILE = _REPO_ROOT / "state" / "daily_state.json"
LIBRARIES_DIR = _REPO_ROOT / "data" / "libraries"

SALAF_SYSTEM_PROMPT = """أنت عالم ومُحرر محتوى دعوي متخصص على منهج أهل السنة والجماعة والطريقة السلفية الصافية (الكتاب والسنة بفهم سلف الأمة، وأقوال العلماء الراسخين كابن كثير والسعدي وابن باز وابن عثيمين والألباني والفوزان).

قواعد إلزامية لا يجوز مخالفتها أبداً:
1. التزم التزاماً تاماً بالمعنى والحكم الشرعي والدليل الموجود في النص المرفق، ولا تغيّر حكماً ولا تخترع حديثاً أو قصة أو سبب نزول غير وارد في المصادر المرفقة.
2. صُغ الشرح بلغة عربية فصيحة، سلسة، ومبسطة تناسب عموم المسلمين في فيديو مدته دقيقتان تقريباً.
3. قواعد النطق والتصوير (مهمة جداً تقنياً):
   - يُمنع منعاً باتاً استخدام الأقواس () أو [] أو ﴿﴾ أو الرموز مثل * # _ - أو أي حروف إنجليزية داخل أي نص.
   - اكتب الأرقام بالكلمات العربية (مثال: ثلاث مرات، مئة مرة) ولا تكتب أرقاماً هندية (١٢٣).
   - اكتب "صلى الله عليه وسلم" أو "رحمه الله" أو "رضي الله عنه" بدون أقواس تماماً.
   - اجعل العنوان (title) مختصراً جداً (لا يتجاوز 45 حرفاً بدون أي أقواس أو إيموجي) حتى لا يخرج عن عرض شاشة الفيديو.
4. أعد JSON فقط بالبنية المطلوبة دون أي كلام قبله أو بعده."""

_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def make_tts_safe(text: str) -> str:
    if not text:
        return ""
    text = text.translate(_DIGIT_MAP)
    text = text.replace("ﷺ", " صلى الله عليه وسلم ")
    text = re.sub(r"[()\[\]﴿﴾«»\"'`]", " ", text)
    text = re.sub(r"[*#@_•/\\|~=+]", " ", text)
    text = re.sub(r"[a-zA-Z]", " ", text)
    text = re.sub(r"\s*([،؛؟!.:])\s*", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_title_safe(title: str, fallback: str) -> str:
    t = make_tts_safe(title)
    t = re.sub(r"[.!؟]", "", t).strip()
    if not t:
        return fallback
    if len(t) > 46:
        words = t[:46].rsplit(" ", 1)[0].strip()
        return words if words else t[:46]
    return t


def format_repeat_ar(rep: int) -> str:
    mapping = {
        1: "",
        2: "مرتين",
        3: "ثلاث مرات",
        4: "أربع مرات",
        5: "خمس مرات",
        7: "سبع مرات",
        10: "عشر مرات",
        33: "ثلاثاً وثلاثين مرة",
        100: "مئة مرة",
    }
    if rep in mapping:
        return mapping[rep]
    return f"{rep} مرة"


def load_library(filename: str) -> list:
    path = LIBRARIES_DIR / filename
    if not path.exists():
        print(f"⚠️ المكتبة غير موجودة: {path}")
        return []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ فشل تحميل {filename}: {e}")
        return []


def pick_unused(items: list, used_ids: list[str], predicate=None) -> tuple[dict | None, list[str]]:
    pool = [i for i in items if (predicate(i) if predicate else True)]
    unused = [i for i in pool if i.get("id") not in used_ids]
    if not unused:
        print("♻️ تم استهلاك جميع العناصر المتاحة في هذا القسم، جاري إعادة التدوير...")
        unused = pool
        used_ids = []
    if not unused:
        return None, used_ids
    picked = random.choice(unused)
    used_ids.append(picked["id"])
    return picked, used_ids


def _extract_json(raw_text: str) -> dict | None:
    if not raw_text:
        return None
    s = raw_text.find("{")
    e = raw_text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(raw_text[s : e + 1])
    except Exception:
        return None


def call_llm_json(user_prompt: str) -> dict | None:
    gh_token = os.getenv("GITHUB_TOKEN", "").strip()
    if gh_token:
        for model in ("openai/gpt-4.1", "openai/gpt-4o", "openai/gpt-4o-mini"):
            try:
                r = requests.post(
                    "https://models.github.ai/inference/chat/completions",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {gh_token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "temperature": 0.3,
                        "max_tokens": 1800,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": SALAF_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                    timeout=60,
                )
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
                    parsed = _extract_json(content)
                    if parsed:
                        print(f"   🤖 تم التوليد عبر GitHub Models ({model})", flush=True)
                        return parsed
                else:
                    print(f"   ⚠️ GitHub Models ({model}): HTTP {r.status_code}", flush=True)
            except Exception as e:
                print(f"   ⚠️ خطأ GitHub Models ({model}): {e}", flush=True)

    raw_keys = os.getenv("GEMINI_API_KEYS", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    for key in keys[:4]:
        for g_model in ("gemini-2.5-flash", "gemini-2.0-flash"):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{g_model}:generateContent?key={key}",
                    json={
                        "systemInstruction": {"parts": [{"text": SALAF_SYSTEM_PROMPT}]},
                        "contents": [{"parts": [{"text": user_prompt}]}],
                        "generationConfig": {
                            "temperature": 0.3,
                            "maxOutputTokens": 2048,
                            "responseMimeType": "application/json",
                        },
                    },
                    timeout=60,
                )
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    txt = "".join(p.get("text", "") for p in parts if p.get("text"))
                    parsed = _extract_json(txt)
                    if parsed:
                        print(f"   🤖 تم التوليد عبر Gemini ({g_model})", flush=True)
                        return parsed
            except Exception:
                continue
    return None


def build_fiqh_episode(used_ids: list[str]) -> tuple[str, list[dict], list[str]]:
    lib = load_library("fiqh-library.json.gz")
    if not lib:
        raise RuntimeError("❌ مكتبة الفقه فارغة.")

    prefer_uthaymeen = (len(used_ids) % 3 == 2)
    subset_pred = (lambda x: x.get("id", "").startswith("salaf-")) if prefer_uthaymeen else (lambda x: x.get("id", "").startswith("binbaz-"))
    item, used_ids = pick_unused(lib, used_ids, predicate=subset_pred)
    if not item:
        item, used_ids = pick_unused(lib, used_ids)

    question = item.get("question", "")
    answer = item.get("answer", "")
    scholar = item.get("scholar", "أهل العلم")
    source = item.get("source", "المصادر الشرعية الموثوقة")

    prompt = f"""لدينا مسألة شرعية موثقة من {scholar} من مصدر {source}:

السؤال أو الموضوع:
{question[:700]}

النص العلمي والفتوى الكاملة:
{answer[:3200]}

المطلوب: حوّل هذه المسألة إلى نص حلقة مرئية مبسطة بالمنهج السني السلفي الصحيح مع الالتزام التام بالحكم والدليل المذكورين في النص فقط:
أعد JSON بالضبط بهذه الحقول:
{{
  "title": "عنوان مختصر جداً أقل من 45 حرفاً يبدأ بـ فقه وفتوى:",
  "hook_question": "تمهيد جذاب يطرح سؤال المسألة بصيغة سؤال مباشر وواضح في جملتين.",
  "ruling_summary": "الحكم الشرعي والفتوى الموثقة بوضوح ودقة مستخلصة من كلام العالم في 3 إلى 4 جمل.",
  "simple_explanation": "الشرح المبسط مع الدليل من الكتاب والسنة وفهم السلف الوارد في النص، والفائدة العملية في حياة المسلم في 4 إلى 6 جمل.",
  "source_credit": "إشارة قصيرة وجميلة في جملة واحدة إلى اسم العالم والمصدر مثل: مستفاد من فتاوى سماحة الشيخ ابن باز رحمه الله."
}}"""

    res = call_llm_json(prompt)
    if res and res.get("hook_question") and res.get("ruling_summary"):
        title = make_title_safe(res.get("title", ""), "فقه وفتوى على منهج السلف")
        segments = [
            {"type": "intro",     "voice": "Puck",       "text": make_tts_safe(res["hook_question"])},
            {"type": "fiqh",      "voice": "Orus",       "text": make_tts_safe(res["ruling_summary"])},
            {"type": "narration", "voice": "Charon",     "text": make_tts_safe(res.get("simple_explanation", ""))},
            {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(res.get("source_credit", f"مستفاد من {scholar}"))},
        ]
    else:
        title = make_title_safe(f"فقه: {item.get('title', '')}", "فقه وفتوى على منهج السلف")
        segments = [
            {"type": "intro",     "voice": "Puck",       "text": make_tts_safe(f"نتوقف اليوم مع مسألة مهمة يكثر السؤال عنها. {question[:220]}")},
            {"type": "fiqh",      "voice": "Orus",       "text": make_tts_safe(answer[:950])},
            {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(f"هذه الفائدة مستفادة من {scholar} في {source}.")},
        ]

    segments = [s for s in segments if s.get("text")]
    return title, segments, used_ids


def _enrich_hadith_from_henc(item: dict) -> dict | None:
    if not item.get("henc_id") or item.get("explanation"):
        grade = (item.get("grade") or "").strip()
        if grade and not any(g in grade for g in ("صحيح", "حسن", "متفق عليه")):
            return None
        return item
    try:
        r = requests.get(
            "https://hadeethenc.com/api/v1/hadeeths/one/",
            params={"language": "ar", "id": item["henc_id"]},
            timeout=20,
        )
        if r.status_code == 200:
            d = r.json()
            grade = (d.get("grade") or "").strip()
            if grade and not any(g in grade for g in ("صحيح", "حسن", "متفق عليه")):
                return None
            item["text"] = (d.get("hadeeth") or item.get("text", "")).strip()
            item["attribution"] = (d.get("attribution") or "").strip()
            item["grade"] = grade or "صحيح"
            item["explanation"] = (d.get("explanation") or "").strip()
            item["hints"] = d.get("hints") or []
            item["words_meanings"] = d.get("words_meanings") or []
    except Exception as e:
        print(f"   ⚠️ تعذر جلب تفاصيل الحديث {item.get('henc_id')}: {e}")
    return item


def build_hadith_episode(used_ids: list[str]) -> tuple[str, list[dict], list[str]]:
    lib = load_library("hadith-library.json.gz")
    if not lib:
        raise RuntimeError("❌ مكتبة الحديث فارغة.")

    item = None
    for _ in range(8):
        cand, used_ids = pick_unused(lib, used_ids)
        if not cand:
            break
        enriched = _enrich_hadith_from_henc(cand)
        if enriched and enriched.get("text") and len(enriched.get("text", "")) <= 1200:
            item = enriched
            break

    if not item:
        raise RuntimeError("❌ لم يتم العثور على حديث صحيح مناسب.")

    matn = item.get("text", "")
    attribution = item.get("attribution", "")
    grade = item.get("grade", "صحيح")
    explanation = item.get("explanation", "")
    hints_raw = item.get("hints") or []
    hints_text = " | ".join(str(h) for h in hints_raw[:6] if h)

    words_raw = item.get("words_meanings") or []
    words_list = []
    for w in words_raw[:6]:
        if isinstance(w, dict):
            words_list.append(f"{w.get('word', '')}: {w.get('meaning', '')}")
        elif isinstance(w, str):
            words_list.append(w)
    words_text = " | ".join(words_list)

    prompt = f"""لدينا حديث نبوي شريف موثق مع شرحه العلمي من موسوعة الأحاديث النبوية:

المتن النبوي الشريف:
{matn}

التخريج والدرجة: {attribution} ، درجته: {grade}
الشرح الموثوق:
{explanation[:2500] if explanation else "حديث صحيح ثابت في السنة النبوية."}

معاني الكلمات: {words_text}
أبرز الفوائد: {hints_text}

المطلوب: أعد إعداد نص الفيديو مع الحفاظ على لفظ الحديث النبوي كما هو دون تحريف، وتقديم شرح مبسط ومؤثر على منهج أهل السنة والجماعة:
{{
  "title": "عنوان مختصر أقل من 45 حرفاً يبدأ بـ حديث وشرحه:",
  "intro": "مقدمة روحانية لطيفة في جملتين تمهد لموضوع الحديث.",
  "hadith_text": "نص الحديث النبوي الشريف كاملاً ومضبوطاً كما ورد، بدون أي أقواس.",
  "sharh_and_benefits": "شرح مبسط لمعنى الحديث ومفرداته وأهم الفوائد العملية المستنبطة منه، مستنداً إلى الشرح والفوائد المرفقة في 4 إلى 6 جمل.",
  "takhrij_credit": "جملة واحدة تذكر التخريج والدرجة والمصدر مثل: رواه البخاري ومسلم وهو حديث صحيح، مستفاد من موسوعة الأحاديث النبوية."
}}"""

    res = call_llm_json(prompt)
    if res and res.get("sharh_and_benefits"):
        title = make_title_safe(res.get("title", ""), "حديث شريف وشرحه")
        segments = [
            {"type": "intro",     "voice": "Puck",       "text": make_tts_safe(res.get("intro", "نتوقف اليوم مع هدي النبي صلى الله عليه وسلم."))},
            {"type": "hadith",    "voice": "Orus",       "text": make_tts_safe(matn)},
            {"type": "narration", "voice": "Charon",     "text": make_tts_safe(res["sharh_and_benefits"])},
            {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(res.get("takhrij_credit", f"التخريج: {attribution}، {grade}، عن موسوعة الأحاديث النبوية."))},
        ]
    else:
        title = make_title_safe(f"حديث: {item.get('title', '')}", "حديث شريف وشرحه")
        segments = [
            {"type": "intro",     "voice": "Puck",       "text": "في هذا المقطع نتدبر حديثاً نبوياً صحيحاً يضيء لنا طريق الهداية."},
            {"type": "hadith",    "voice": "Orus",       "text": make_tts_safe(matn)},
            {"type": "narration", "voice": "Charon",     "text": make_tts_safe(explanation[:1000] if explanation else f"حديث عظيم من هدي نبينا صلى الله عليه وسلم، {hints_text}")},
            {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(f"{attribution}، ودرجته {grade}، من موسوعة الأحاديث النبوية.")},
        ]

    segments = [s for s in segments if s.get("text")]
    return title, segments, used_ids


def _fetch_extra_tafsir(edition: str, surah: int, ayah: int) -> str:
    url = f"https://cdn.jsdelivr.net/gh/spa5k/tafsir_api@main/tafsir/{edition}/{surah}/{ayah}.json"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            txt = (r.json().get("text") or "").strip()
            return re.sub(r"<[^>]+>", " ", txt)
    except Exception:
        pass
    return ""


def build_quran_episode(used_ids: list[str]) -> tuple[str, list[dict], list[str]]:
    lib = load_library("ayah-library.json.gz")
    if not lib:
        raise RuntimeError("❌ مكتبة الآيات فارغة.")

    # طول مثالي للآية (45-340 حرفاً) حتى لا تتقطع أثناء القراءة عبر Gemini في نسخة اليوتيوب
    item, used_ids = pick_unused(
        lib,
        used_ids,
        predicate=lambda i: 45 <= len(i.get("text", "")) <= 340 and len(i.get("tafsir", "")) >= 80,
    )
    if not item:
        raise RuntimeError("❌ لم يتم العثور على آية صالحة.")

    s_num = int(item["surah_number"])
    a_num = int(item["ayah_number"])
    s_name = item["surah_name"].replace("سورة ", "").replace("سُورَةُ ", "").strip()
    ayah_text = item["text"]
    muyassar = item["tafsir"]

    saadi = _fetch_extra_tafsir("ar-tafseer-al-saddi", s_num, a_num)
    ibn_kathir = _fetch_extra_tafsir("ar-tafsir-ibn-kathir", s_num, a_num)

    prompt = f"""لدينا آية كريمة تم اختيارها عشوائياً لفقرة آية وحكاية:

السورة: سورة {s_name} ، الآية رقم {a_num}
نص الآية الكريمة:
{ayah_text}

التفسير الميسر:
{muyassar[:1000]}

تفسير العلامة عبد الرحمن السعدي رحمه الله:
{saadi[:1400] if saadi else "غير متوفر لهذه الآية"}

مقتطف من تفسير الحافظ ابن كثير رحمه الله:
{ibn_kathir[:1400] if ibn_kathir else "غير متوفر لهذه الآية"}

المطلوب: اكتب نص حلقة آية وحكاية بأسلوب إيماني رقيق ومبسط على عقيدة أهل السنة والجماعة، بالاعتماد حصراً على التفاسير الموثقة أعلاه (ولا تخترع قصة أو سبب نزول غير مذكور في التفاسير):
{{
  "title": "عنوان مختصر أقل من 45 حرفاً مثل: آية وحكاية: سورة {s_name}",
  "intro": "مقدمة تمهيدية دافئة في جملتين تهيئ القلب لسماع الآية الكريمة وتذكر اسم السورة.",
  "tafsir_story": "شرح مبسط وشيق لمعنى الآية وحكايتها أو سبب نزولها إن ورد في التفسير، مع بيان اللطائف الإيمانية من تفسير السعدي والميسر وابن كثير في 4 إلى 6 جمل.",
  "takeaway": "الدرس العملي للمسلم في حياته اليومية مع ذكر مصدر التفسير في جملة أو جملتين."
}}"""

    res = call_llm_json(prompt)
    if res and res.get("tafsir_story"):
        title = make_title_safe(res.get("title", ""), f"آية وحكاية: سورة {s_name}")
        intro_txt = make_tts_safe(res.get("intro", f"في سورة {s_name}، نستمع الآن إلى آية عظيمة من كتاب الله تعالى."))
        tafsir_txt = make_tts_safe(res["tafsir_story"])
        takeaway_txt = make_tts_safe(res.get("takeaway", "نستفيد هذا المعنى من التفسير الميسر وتفسير السعدي رحمه الله."))
    else:
        title = make_title_safe(f"آية وحكاية: سورة {s_name}", f"آية وحكاية: سورة {s_name}")
        intro_txt = f"في سورة {s_name}، يقول الله تعالى:"
        tafsir_txt = make_tts_safe(f"يقول التفسير الميسر في معنى هذه الآية الكريمة: {muyassar[:850]}")
        takeaway_txt = "فلنتدبر كتاب الله ونعمل بهداياته في حياتنا اليومية."

    segments = [
        {"type": "intro", "voice": "Puck", "text": intro_txt},
        {
            "type": "quran",
            "surah": s_num,
            "surah_name": s_name,
            "ayah_numbers": [a_num],
            "display": f"{s_name}: {a_num}",
        },
        {"type": "narration", "voice": "Charon",     "text": tafsir_txt},
        {"type": "summary",   "voice": "Rasalgethi", "text": takeaway_txt},
    ]
    return title, segments, used_ids


def build_adhkar_episode(used_ids: list[str], state: dict) -> tuple[str, list[dict], list[str]]:
    lib = load_library("adhkar-library.json.gz")
    if not lib:
        raise RuntimeError("❌ مكتبة الأذكار فارغة.")

    slots = ["sabah", "masaa", "sleep"]
    slot_names = {
        "sabah": "أذكار الصباح",
        "masaa": "أذكار المساء",
        "sleep": "أذكار قبل النوم",
    }
    slot_idx = int(state.get("adhkar_slot_idx", 0)) % 3
    current_slot = slots[slot_idx]
    state["adhkar_slot_idx"] = slot_idx + 1
    slot_label = slot_names[current_slot]

    picked_list = []
    for _ in range(3):
        item, used_ids = pick_unused(
            lib,
            used_ids,
            predicate=lambda i: i.get("slot") == current_slot and 20 <= len(i.get("text", "")) <= 500,
        )
        if item and item not in picked_list:
            picked_list.append(item)

    if not picked_list:
        raise RuntimeError(f"❌ لم يتم العثور على أذكار لقسم {slot_label}.")

    adhkar_bundle = "\n".join(
        f"- الذكر {idx+1}: {x['text']} | التكرار: {x.get('repeat', 1)} | الفضل: {x.get('bless', '')} | المصدر: {x.get('reference', 'حصن المسلم')}"
        for idx, x in enumerate(picked_list)
    )

    prompt = f"""لدينا باقة مختارة من {slot_label} الصحيحة من حصن المسلم والسنة النبوية:

{adhkar_bundle}

المطلوب: إعداد حلقة روحانية هادئة ومبسطة لقسم {slot_label} بحيث يُحفظ نص الذكر النبوي كما ورد دون تحريف، ويُشرح معناه وفضله ببساطة:
{{
  "title": "عنوان مختصر أقل من 45 حرفاً يبدأ بـ {slot_label}",
  "intro": "مقدمة روحانية لطيفة في جملتين تذكّر بأهمية {slot_label} وحفظ الله للعبد بها.",
  "items": [
    {{
      "lead_in": "تمهيد قصير في جملة واحدة يذكر عدد التكرار بالكلمات إن زاد عن واحد والفضل إن وُجد.",
      "dhikr_exact": "نص الذكر الأصلي كما هو بدون أي أقواس أو تغيير.",
      "meaning_note": "توضيح مبسط لمعنى الذكر وأثره في القلب في جملة أو جملتين."
    }}
  ],
  "closing": "خاتمة لطيفة تشجع على المداومة على {slot_label} مع الإشارة إلى حصن المسلم والسنة الصحيحة."
}}"""

    res = call_llm_json(prompt)
    segments = []
    if res and isinstance(res.get("items"), list) and len(res["items"]) > 0:
        title = make_title_safe(res.get("title", ""), f"{slot_label} من السنة الصحيحة")
        segments.append({"type": "intro", "voice": "Puck", "text": make_tts_safe(res.get("intro", f"حافظ على {slot_label} فهي حصن المسلم."))})
        for idx, gen_item in enumerate(res["items"][:3]):
            src_item = picked_list[idx] if idx < len(picked_list) else picked_list[-1]
            lead = make_tts_safe(gen_item.get("lead_in", ""))
            if lead:
                segments.append({"type": "narration", "voice": "Charon", "text": lead})
            segments.append({"type": "adhkar", "voice": "Algieba", "text": make_tts_safe(src_item["text"])})
            note = make_tts_safe(gen_item.get("meaning_note", ""))
            if note:
                segments.append({"type": "narration", "voice": "Charon", "text": note})
        segments.append({"type": "summary", "voice": "Rasalgethi", "text": make_tts_safe(res.get("closing", f"تلك باقة من {slot_label} الثابتة في السنة الصحيحة وحصن المسلم."))})
    else:
        title = make_title_safe(f"{slot_label} المباركة", slot_label)
        segments.append({"type": "intro", "voice": "Puck", "text": f"في هذا المقطع نستذكر باقة مباركة من {slot_label} الثابتة عن النبي صلى الله عليه وسلم."})
        for x in picked_list:
            rep = int(x.get("repeat", 1) or 1)
            rep_txt = format_repeat_ar(rep)
            bless = x.get("bless", "")
            lead = f"يُقال هذا الذكر {rep_txt}. {bless}".strip() if rep_txt else bless
            if lead.strip():
                segments.append({"type": "narration", "voice": "Charon", "text": make_tts_safe(lead)})
            segments.append({"type": "adhkar", "voice": "Algieba", "text": make_tts_safe(x["text"])})
        segments.append({"type": "summary", "voice": "Rasalgethi", "text": f"حافظ على {slot_label} يومياً، من كتاب حصن المسلم والسنة النبوية الصحيحة."})

    segments = [s for s in segments if s.get("text")]
    return title, segments, used_ids


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    state = {
        "run_count": 0,
        "adhkar_slot_idx": 0,
        "used_ids": {"fiqh": [], "hadith": [], "quran": [], "adhkar": []},
    }
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            state.update(saved)
            for k in ("fiqh", "hadith", "quran", "adhkar"):
                state.setdefault("used_ids", {}).setdefault(k, [])
        except Exception:
            pass

    type_names = ["fiqh", "hadith", "quran", "adhkar"]
    forced = os.getenv("FORCE_CONTENT_TYPE", "auto").strip().lower()
    if forced in type_names:
        current_type = forced
    else:
        current_type = type_names[state["run_count"] % len(type_names)]

    used_list = state["used_ids"].get(current_type, [])
    state["run_count"] += 1
    print(f"🔄 التشغيل #{state['run_count']} | النوع: {current_type} | المستخدم سابقاً: {len(used_list)}", flush=True)

    if current_type == "fiqh":
        title, segments, used_list = build_fiqh_episode(used_list)
    elif current_type == "hadith":
        title, segments, used_list = build_hadith_episode(used_list)
    elif current_type == "quran":
        title, segments, used_list = build_quran_episode(used_list)
    else:
        title, segments, used_list = build_adhkar_episode(used_list, state)

    segments.append({
        "type": "cta",
        "voice": "Puck",
        "text": "قال رسول الله صلى الله عليه وسلم: من دل على خير فله مثل أجر فاعله. شارك المقطع لتعم الفائدة. ملاك المعرفة.",
    })

    ep_data = {
        "episode_number": state["run_count"],
        "story_id": f"daily_{current_type}",
        "title": title,
        "segments": segments,
    }

    state["used_ids"][current_type] = used_list
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"daily-{current_type}-{timestamp}.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")

    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم توليد الحلقة بنجاح: {output_path} | العنوان: {title}", flush=True)


if __name__ == "__main__":
    main()
