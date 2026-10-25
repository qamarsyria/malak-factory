"""توليد حلقة: خطة → سرد → تدقيق → مقاطع → JSON."""
import os
import re
import sys
import json
import random
import traceback
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool  # noqa: E402
from model_discovery import discover_and_cache  # noqa: E402
from story_library import load_library, load_state, save_state, get_current_story  # noqa: E402
from outro_templates import OUTRO_TEMPLATES  # noqa: E402
from utilities import notify_telegram, get_breaker  # noqa: E402

OUTPUT_DIR = _REPO_ROOT / "data/stories/generated"
PENDING_DIR = _REPO_ROOT / "state/pending"
OUT_FILE_PATH = _REPO_ROOT / "out/latest_episode.txt"

SURAH_NAMES = {
    1: "الفاتحة", 2: "البقرة", 3: "آل عمران", 4: "النساء", 5: "المائدة",
    6: "الأنعام", 7: "الأعراف", 8: "الأنفال", 9: "التوبة", 10: "يونس",
    11: "هود", 12: "يوسف", 13: "الرعد", 14: "إبراهيم", 15: "الحجر",
    16: "النحل", 17: "الإسراء", 18: "الكهف", 19: "مريم", 20: "طه",
    21: "الأنبياء", 22: "الحج", 23: "المؤمنون", 24: "النور", 25: "الفرقان",
    26: "الشعراء", 27: "النمل", 28: "القصص", 29: "العنكبوت", 30: "الروم",
    31: "لقمان", 32: "السجدة", 33: "الأحزاب", 34: "سبأ", 35: "فاطر",
    36: "يس", 37: "الصافات", 38: "ص", 39: "الزمر", 40: "غافر",
    41: "فصلت", 42: "الشورى", 43: "الزخرف", 44: "الدخان", 45: "الجاثية",
    46: "الأحقاف", 47: "محمد", 48: "الفتح", 49: "الحجرات", 50: "ق",
    51: "الذاريات", 52: "الطور", 53: "النجم", 54: "القمر", 55: "الرحمن",
    56: "الواقعة", 57: "الحديد", 58: "المجادلة", 59: "الحشر", 60: "الممتحنة",
    61: "الصف", 62: "الجمعة", 63: "المنافقون", 64: "التغابن", 65: "الطلاق",
    66: "التحريم", 67: "الملك", 68: "القلم", 69: "الحاقة", 70: "المعارج",
    71: "نوح", 72: "الجن", 73: "المزمل", 74: "المدثر", 75: "القيامة",
    76: "الإنسان", 77: "المرسلات", 78: "النبأ", 79: "النازعات", 80: "عبس",
    81: "التكوير", 82: "الانفطار", 83: "المطففين", 84: "الانشقاق", 85: "البروج",
    86: "الطارق", 87: "الأعلى", 88: "الغاشية", 89: "الفجر", 90: "البلد",
    91: "الشمس", 92: "الليل", 93: "الضحى", 94: "الشرح", 95: "التين",
    96: "العلق", 97: "القدر", 98: "البينة", 99: "الزلزلة", 100: "العاديات",
    101: "القارعة", 102: "التكاثر", 103: "العصر", 104: "الهمزة", 105: "الفيل",
    106: "قريش", 107: "الماعون", 108: "الكوثر", 109: "الكافرون", 110: "النصر",
    111: "المسد", 112: "الإخلاص", 113: "الفلق", 114: "الناس",
}


def get_working_models():
    return discover_and_cache(force=False)


def call_gemini(prompt, is_json=True, timeout=180, max_tokens=8192):
    pool = get_pool()
    breaker = get_breaker()
    for model in get_working_models()[:5]:
        if breaker.is_open(f"model:{model}"):
            continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = (f"https://generativelanguage.googleapis.com/v1beta/"
                       f"models/{model}:generateContent?key={key}")
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.4,
                        "maxOutputTokens": max_tokens,
                    },
                }
                if is_json:
                    body["generationConfig"]["responseMimeType"] = "application/json"
                r = requests.post(url, json=body, timeout=timeout)

                if r.status_code == 200:
                    parts = (r.json().get("candidates", [{}])[0]
                             .get("content", {}).get("parts", []))
                    if not parts:
                        break
                    text = parts[0].get("text", "")
                    if not text:
                        break
                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s:
                            break
                        text = text[s:e + 1]
                    try:
                        parsed = json.loads(text) if is_json else text
                        pool._advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError:
                        break
                elif r.status_code == 429:
                    pool.mark_failed(3600)
                elif r.status_code in (404, 503):
                    breaker.record_failure(f"model:{model}")
                    break
            except Exception:
                pass
    return None


def call_huggingface(prompt, is_json=True):
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        return None
    model_id = "mistralai/Mistral-7B-Instruct-v0.3"
    url = f"https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {hf_token}",
                     "Content-Type": "application/json"},
            json={"model": model_id,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.4, "max_tokens": 6000},
            timeout=180,
        )
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            if is_json:
                s, e = text.find("{"), text.rfind("}")
                if s != -1 and e > s:
                    text = text[s:e + 1]
            return json.loads(text) if is_json else text
    except Exception:
        pass
    return None


def call_llm(prompt, is_json=True, timeout=180, max_tokens=8192):
    return call_gemini(prompt, is_json, timeout, max_tokens) \
        or call_huggingface(prompt, is_json)


def normalize_arabic_numerals(text):
    return text.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))


def verify_ayah(surah_num, ayah_num):
    if not (1 <= surah_num <= 114) or not (1 <= ayah_num <= 286):
        return None, False
    local_name = SURAH_NAMES.get(surah_num)
    if not local_name:
        return None, False
    try:
        r = requests.get(
            f"https://api.alquran.cloud/v1/ayah/{surah_num}:{ayah_num}",
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            if data and data.get("numberInSurah") == ayah_num:
                return local_name, True
    except Exception:
        pass
    return local_name, True


def split_narration(text, max_chars=700):
    """يقسم النص الطويل إلى مقاطع صغيرة بحدود الجمل."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r'\n\s*\n', text)
    segments = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        sentences = re.split(r'(?<=[.!؟])\s+', para)
        current = ""
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(sent) > max_chars:
                if current:
                    segments.append(current)
                    current = ""
                parts = re.split(r'(?<=[،؛])\s+', sent)
                for p in parts:
                    if len(current) + len(p) + 1 <= max_chars:
                        current = (current + " " + p).strip() if current else p
                    else:
                        if current:
                            segments.append(current)
                        current = p
            elif len(current) + len(sent) + 1 <= max_chars:
                current = (current + " " + sent).strip() if current else sent
            else:
                if current:
                    segments.append(current)
                current = sent
        if current:
            segments.append(current)
    return [s for s in segments if len(s) > 5]


def parse_script_with_verses(script_text):
    text = normalize_arabic_numerals(script_text)

    patterns = [
        r'\[آ[يی]ة[:\s]*(\d+)\s*[:\-،]\s*(\d+)\]',
        r'[\(（]آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)[\)）]',
        r'\[آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)\s*\]',
    ]

    parts = None
    for pat in patterns:
        parts = re.split(pat, text)
        if len(parts) > 1:
            print(f"   ✓ النمط: {pat}", flush=True)
            break

    if not parts or len(parts) == 1:
        suspicious = re.findall(r'[\[\(]آ[^\]]{0,30}[\]\)]', text)
        if suspicious:
            print(f"   ⚠️ صيغ مشبوهة: {suspicious[:3]}", flush=True)
        segments = [{"type": "narration", "text": c} for c in split_narration(text)]
        return segments

    segments, i, quran_count = [], 0, 0
    while i < len(parts):
        if i % 3 == 0:
            chunk = parts[i].strip()
            chunk = re.sub(r'[\[\(]آ[يی]ة[^\]]*[\]\)]', '', chunk).strip()
            if len(chunk) > 5:
                for piece in split_narration(chunk):
                    segments.append({"type": "narration", "text": piece})
            i += 1
        else:
            try:
                surah, ayah = int(parts[i]), int(parts[i + 1])
            except (ValueError, IndexError):
                i += 2
                continue
            sname, ok = verify_ayah(surah, ayah)
            if ok:
                segments.append({
                    "type": "quran",
                    "surah": surah,
                    "surah_name": sname,
                    "ayah_numbers": [ayah],
                    "display": f"{sname}: {ayah}",
                })
                quran_count += 1
                print(f"   ✓ آية {surah}:{ayah} ({sname})", flush=True)
            else:
                print(f"   ⚠️ آية غير صالحة {surah}:{ayah}", flush=True)
            i += 2

    print(f"   📊 {quran_count} آية، {len(segments)} مقطع", flush=True)
    return segments


def review_in_chunks(text, chunk_size=4500):
    if len(text) <= chunk_size:
        result = call_llm(
            "صحح الأخطاء الإملائية والنحوية فقط. "
            "حافظ على علامات [آية: X:Y] كما هي، ولا تحذف أي جملة. "
            "أعد النص المصحح كاملاً:\n\n" + text,
            is_json=False, timeout=180, max_tokens=8192,
        )
        if result and len(result) > len(text) * 0.8:
            return result
        return text

    paragraphs = text.split("\n\n")
    chunks, current = [], ""
    for p in paragraphs:
        if len(current) + len(p) > chunk_size and current:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)

    reviewed = []
    for idx, chunk in enumerate(chunks):
        print(f"   🔍 تدقيق {idx + 1}/{len(chunks)}...", flush=True)
        result = call_llm(
            "صحح الأخطاء الإملائية والنحوية فقط. "
            "حافظ على علامات [آية: X:Y] كما هي، ولا تحذف أي جملة. "
            "أعد النص المصحح كاملاً:\n\n" + chunk,
            is_json=False, timeout=180, max_tokens=8192,
        )
        if result and len(result) > len(chunk) * 0.8:
            reviewed.append(result)
        else:
            print(f"   ⚠️ الجزء {idx + 1} غير مقبول، سنستخدم الأصل", flush=True)
            reviewed.append(chunk)
    return "\n\n".join(reviewed)


def build_plan_prompt(story, ep_num):
    return f"""أنت مؤرخ إسلامي. اكتب خطة للحلقة {ep_num} من "{story['title']}".
اعتمد على: {', '.join(story.get('sources', []))}
غطِّ هذه المواضيع: {', '.join(story.get('key_topics', [])[:5])}
ابتعد عن الإسرائيليات.
أعد JSON فقط: {{"title": "عنوان", "summary": "ملخص (200-300 كلمة)"}}"""


def build_script_prompt(story, plan, ep_num):
    topics = ", ".join(story.get("key_topics", []))
    sources = ", ".join(story.get("sources", []))
    return f"""أنت كاتب سيناريو إسلامي محترف، تكتب سرداً درامياً موثقاً.

القصة: {story['title']}
الفئة: {story.get('category', '')}
المصادر: {sources}
المواضيع: {topics}
الحلقة: {ep_num} من {story.get('target_episodes', 40)}
الخطة: {plan['title']}
{plan['summary']}

قواعد صارمة جداً:
1. اعتمد فقط على المصادر المذكورة. تجنب الإسرائيليات والروايات الضعيفة.
2. اكتب 2000-2400 كلمة سرد درامي.
3. **إلزامي: أدرج 6 آيات على الأقل**. لكل آية، استخدم الصيغة الحرفية:
   [آية: <رقم السورة>:<رقم الآية>]
   مثال: [آية: 3:35]
4. ضع العلامة مباشرة بعد الجملة التي تدعمها الآية.
5. لا تكتب [آية] بدون أرقام. لا تستخدم صيغة أخرى.
6. ابدأ بمقدمة مشوقة واختم بموعظة.
7. **التشكيل الإلزامي**: يجب كتابة النص بالكامل مع التشكيل (الحركات: فتحة، ضمة، كسرة، سكون) لضمان النطق السليم.

مثال صحيح:
"ثُمَّ تَوَجَّهَ مُوسَى إِلَى رَبِّهِ دَاعِياً فِي خَلْوَتِهِ، فَاسْتَجَابَ اللَّهُ لَهُ وَوَعَدَهُ بِالنَّصْرِ [آية: 20:36]. وَبَيْنَمَا كَانَ فِي الْوَادِي الْمُقَدَّسِ، نُودِيَ مِنْ جَانِبِ الطُّورِ الْأَيْمَنِ [آية: 28:30]."

اكتب النص فقط دون مقدمات."""


def save_episode(ep_data):
    story_id = ep_data["story_id"]
    ep_num = ep_data["episode_number"]

    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(ep_data, ensure_ascii=False, indent=2)
    pending_path = PENDING_DIR / f"{story_id}-{ep_num:02d}.json"
    output_path = OUTPUT_DIR / f"{story_id}-{ep_num:02d}.json"

    pending_path.write_text(payload, encoding="utf-8")
    output_path.write_text(payload, encoding="utf-8")

    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    return output_path


def main():
    library = load_library()
    state = load_state()
    story = get_current_story(library, state)
    ep_num = state.get("episode_num", 1)

    save_state(state)

    print(f"🚀 {story['title']} — الحلقة {ep_num}/{story.get('target_episodes', 40)}",
          flush=True)

    pending_path = PENDING_DIR / f"{story['id']}-{ep_num:02d}.json"
    if pending_path.exists():
        try:
            ep_data = json.loads(pending_path.read_text(encoding="utf-8"))
            if ep_data.get("segments") and len(ep_data["segments"]) > 3:
                print(f"♻️ استئناف من pending: {pending_path.name}", flush=True)
                out = save_episode(ep_data)
                print(f"✅ أُعيد استخدام: {out}", flush=True)
                return
        except Exception as e:
            print(f"⚠️ pending تالف، سنعيد التوليد: {e}", flush=True)

    plan = call_llm(build_plan_prompt(story, ep_num))
    if not plan or "title" not in plan or "summary" not in plan \
            or len(str(plan.get("summary", ""))) < 80:
        plan = {
            "title": f"الحلقة {ep_num}",
            "summary": f"نتعرف في هذه الحلقة على تفاصيل من {story['title']}.",
        }
    print(f"📜 {plan['title']}", flush=True)

    print("✍️ توليد السرد...", flush=True)
    script_text = None
    segments = []
    for attempt in range(1, 3):
        script_text = call_llm(
            build_script_prompt(story, plan, ep_num),
            is_json=False, timeout=180, max_tokens=8192,
        )
        if not script_text:
            print(f"   ⚠️ محاولة {attempt}: فشل التوليد", flush=True)
            continue

        segments = parse_script_with_verses(script_text)
        qcount = sum(1 for s in segments if s["type"] == "quran")
        print(f"   محاولة {attempt}: {qcount} آية، {len(segments)} مقطع", flush=True)
        if qcount >= 3 and len(segments) >= 5:
            break

    if not segments:
        notify_telegram(f"فشل توليد {story['title']} ح{ep_num}", "error")
        sys.exit(1)

    if not script_text:
        notify_telegram(f"سرد فارغ {story['title']} ح{ep_num}", "error")
        sys.exit(1)

    print("🔍 تدقيق...", flush=True)
    corrected = review_in_chunks(script_text)
    if corrected and corrected != script_text and len(corrected) > len(script_text) * 0.7:
        corrected_segs = parse_script_with_verses(corrected)
        cq = sum(1 for s in corrected_segs if s["type"] == "quran")
        orig_q = sum(1 for s in segments if s["type"] == "quran")
        if cq >= orig_q and len(corrected_segs) >= len(segments) * 0.8:
            segments = corrected_segs
            print(f"   ✅ استُخدم النص المصحح ({cq} آية)", flush=True)
        else:
            print("   ⚠️ النص المصحح أقل جودة، سنستخدم الأصل", flush=True)

    outro = random.choice(OUTRO_TEMPLATES)
    segments.append({
        "type": "cta",
        "text": outro["text"],
        "emotion_hint": outro["voice_style"],
        "visual": "mosque",
        "sfx": "",
    })

    segments.append({
        "type": "teaser",
        "text": f"الحلقة القادمة من {story['title']}.",
        "emotion_hint": "(in a calm, inviting tone)",
        "next_episode_title": f"الحلقة {ep_num + 1}",
        "next_episode_source": story["title"],
        "url": "https://qamarsyria.github.io/qamar-malak/",
        "platform_name": "ملاك المعرفة",
    })

    ep_data = {
        "episode_number": ep_num,
        "story_id": story["id"],
        "story_title": story["title"],
        "title": f"{story['title']} — {plan['title']}",
        "segments": segments,
    }
    out = save_episode(ep_data)
    print(f"✅ JSON: {out}", flush=True)

    narr = sum(1 for s in segments if s["type"] == "narration")
    qr = sum(1 for s in segments if s["type"] == "quran")
    avg = (sum(len(s.get("text", "")) for s in segments if s["type"] == "narration")
           / max(1, narr))
    print(f"\n{'='*50}", flush=True)
    print(f"📊 التقرير النهائي:", flush=True)
    print(f"   مقاطع السرد: {narr}", flush=True)
    print(f"   الآيات: {qr}", flush=True)
    print(f"   متوسط طول السرد: {avg:.0f} حرفاً", flush=True)
    print(f"   مقاطع الفيديو المتوقعة: {len(segments)}", flush=True)
    print(f"{'='*50}\n", flush=True)

    if qr == 0:
        notify_telegram(f"⚠️ حلقة {story['title']} ح{ep_num} بلا آيات", "warn")
    if avg > 750:
        notify_telegram(f"⚠️ مقاطع طويلة ({avg:.0f} حرفاً)", "warn")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        notify_telegram(f"فشل generate_story: {e}", "error")
        sys.exit(1)
