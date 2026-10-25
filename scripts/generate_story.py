import os, json, requests, sys, time, random, traceback, re
from pathlib import Path
from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from story_library import (
    load_library, load_state, save_state, get_current_story,
    should_advance_story, advance_story,
)
from outro_templates import OUTRO_TEMPLATES
from utilities import retry, notify_telegram, get_breaker

OUTPUT_DIR = Path("data/stories/generated")
PENDING_DIR = Path("state/pending")
MODELS_CACHE_FILE = Path("state/models_cache.json")


def get_working_models():
    return discover_and_cache(force=False)


def call_gemini(prompt, is_json=True, timeout=120, max_tokens=8192):
    pool = get_pool()
    breaker = get_breaker()
    for model in get_working_models()[:5]:
        if breaker.is_open(f"model:{model}"):
            continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens},
                }
                if is_json:
                    body["generationConfig"]["responseMimeType"] = "application/json"
                r = requests.post(url, json=body, timeout=timeout)
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts:
                        break
                    text = parts[0].get("text", "")
                    if not text:
                        break
                    if is_json:
                        start, end = text.find("{"), text.rfind("}")
                        if start == -1 or end <= start:
                            break
                        text = text[start:end+1]
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
            headers={"Authorization": f"Bearer {hf_token}", "Content-Type": "application/json"},
            json={"model": model_id, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.4, "max_tokens": 6000},
            timeout=180,
        )
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            if is_json:
                s, e = text.find("{"), text.rfind("}")
                if s != -1 and e > s:
                    text = text[s:e+1]
            return json.loads(text) if is_json else text
    except Exception:
        pass
    return None


def call_llm(prompt, is_json=True, timeout=120, max_tokens=8192):
    return call_gemini(prompt, is_json, timeout, max_tokens) or call_huggingface(prompt, is_json)


def verify_ayah(surah_num, ayah_num):
    if not (1 <= surah_num <= 114) or not (1 <= ayah_num <= 286):
        return f"سورة {surah_num}", False
    try:
        r = requests.get(f"https://api.alquran.cloud/v1/ayah/{surah_num}:{ayah_num}", timeout=10)
        if r.status_code == 200:
            name = r.json()["data"]["surah"]["name"].replace("سُورَةُ ", "").strip()
            return name, True
    except Exception:
        pass
    return f"سورة {surah_num}", False


def parse_script_with_verses(script_text):
    pattern = r"\[آ[يی]ة[:\s]*(\d+)[:\s]+(\d+)\]"
    parts = re.split(pattern, script_text)
    segments, i = [], 0
    while i < len(parts):
        if i % 3 == 0:
            text = re.sub(r"\[آ[يی]ة[^\]]*\]", "", parts[i]).strip()
            if len(text) > 5:
                segments.append({"type": "narration", "text": text})
            i += 1
        else:
            try:
                surah, ayah = int(parts[i]), int(parts[i+1])
            except (ValueError, IndexError):
                i += 2
                continue
            sname, ok = verify_ayah(surah, ayah)
            if ok:
                segments.append({
                    "type": "quran", "surah": surah, "surah_name": sname,
                    "ayah_numbers": [ayah], "display": f"{sname}: {ayah}",
                })
            else:
                print(f"   ⚠️ آية غير محققة: {surah}:{ayah}", flush=True)
            i += 2
    return segments


def review_in_chunks(text, chunk_size=4500):
    """تدقيق النص على دفعات — يغطي كل النص دون اقتطاع."""
    if len(text) <= chunk_size:
        return call_llm(
            f"صحح الأخطاء الإملائية والنحوية فقط. حافظ على [آية: X:Y] كما هي. أعد النص فقط:\n\n{text}",
            is_json=False, timeout=120, max_tokens=8192,
        ) or text

    # تقسيم على فقرات
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
        print(f"   🔍 تدقيق الجزء {idx+1}/{len(chunks)}...", flush=True)
        result = call_llm(
            f"صحح الأخطاء الإملائية والنحوية فقط. حافظ على [آية: X:Y] كما هي. أعد النص فقط:\n\n{chunk}",
            is_json=False, timeout=120, max_tokens=8192,
        )
        reviewed.append(result if result and len(result.split()) > 50 else chunk)
    return "\n\n".join(reviewed)


def build_prompt(story, plan, ep_num):
    topics = ", ".join(story.get("key_topics", []))
    sources = ", ".join(story.get("sources", []))
    return f"""أنت كاتب سيناريو إسلامي محترف، تكتب سرداً درامياً موثقاً.

القصة: {story['title']}
الفئة: {story.get('category', '')}
المصادر: {sources}
المواضيع الرئيسية: {topics}
رقم الحلقة: {ep_num} من {story.get('target_episodes', 40)}
الخطة: {plan['title']}
{plan['summary']}

قواعد صارمة:
1. اعتمد على المصادر المذكورة فقط، وابتعد عن الإسرائيليات والروايات الضعيفة.
2. اكتب نصاً سردياً درامياً مفصلاً (2000-2400 كلمة)، ووسّع في وصف المشاعر والمشاهد والحوار.
3. عند إدراج آية، ضع العلامة بهذا الشكل بالضبط: [آية: 3:35]
4. عدد الآيات من 5 إلى 8 فقط.
5. ابدأ بمقدمة تشد المستمع، واختم بموعظة.
6. لا تختصر الأحداث، ولا تكرر ما سبق من الحلقات.

اكتب النص فقط دون أي مقدمات."""


def main():
    library = load_library()
    state = load_state()
    story = get_current_story(library, state)

    if should_advance_story(story, state):
        advance_story(state, story)
        save_state(state)
        story = get_current_story(library, state)

    ep_num = state.get("episode_num", 1)
    print(f"🚀 بدء: {story['title']} — الحلقة {ep_num}/{story.get('target_episodes', 40)}", flush=True)

    pending_file = PENDING_DIR / f"pending-{story['id']}-{ep_num}.json"
    PENDING_DIR.mkdir(parents=True, exist_ok=True)

    # 1) خطة الحلقة (من pending إن وجدت)
    plan = None
    if pending_file.exists():
        try:
            plan = json.loads(pending_file.read_text(encoding="utf-8"))
            print("♻️ استئناف من الخطة المحفوظة", flush=True)
        except Exception:
            plan = None

    if not plan:
        plan_prompt = f"""أنت مؤرخ إسلامي. اكتب خطة للحلقة {ep_num} من "{story['title']}".
اعتمد على: {', '.join(story.get('sources', []))}
غطِّ هذه المواضيع: {', '.join(story.get('key_topics', [])[:5])}
ابتعد عن الإسرائيليات.
أعد JSON فقط: {{"title": "عنوان", "summary": "ملخص (200-300 كلمة)"}}"""
        plan = call_llm(plan_prompt)
        if not plan or "title" not in plan or "summary" not in plan or len(plan.get("summary", "")) < 80:
            plan = {
                "title": f"الحلقة {ep_num}",
                "summary": f"نتعرف في هذه الحلقة على تفاصيل من {story['title']}.",
            }
        pending_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    print(f"📜 {plan['title']}", flush=True)

    # 2) السرد
    print("✍️ توليد السرد...", flush=True)
    script_text = call_llm(build_prompt(story, plan, ep_num), is_json=False, timeout=180, max_tokens=8192)
    if not script_text:
        notify_telegram(f"فشل توليد سرد {story['title']} ح{ep_num}", "error")
        sys.exit(1)

    # 3) تدقيق مجزأ
    print("🔍 تدقيق...", flush=True)
    corrected = review_in_chunks(script_text)
    if corrected and len(corrected) > len(script_text) * 0.7:
        script_text = corrected
        print("   ✅ تم التدقيق", flush=True)

    # 4) استخراج
    print("🛠️ استخراج المقاطع...", flush=True)
    segments = parse_script_with_verses(script_text)
    if not segments:
        notify_telegram(f"لا مقاطع في {story['title']} ح{ep_num}", "error")
        sys.exit(1)

    # 5) النص الختامي (cta)
    outro = random.choice(OUTRO_TEMPLATES)
    segments.append({
        "type": "cta",
        "text": outro["text"],
        "emotion_hint": outro["voice_style"],
        "visual": "mosque",
        "sfx": "",
    })

    # 6) التشويق
    segments.append({
        "type": "teaser",
        "text": f"الحلقة القادمة من {story['title']}.",
        "emotion_hint": "(in a calm, inviting tone)",
        "next_episode_title": f"الحلقة {ep_num + 1}",
        "next_episode_source": story["title"],
        "url": "https://qamarsyria.github.io/qamar-malak/",
        "platform_name": "ملاك المعرفة",
    })

    # 7) حفظ
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{story['id']}-{ep_num:02d}.json"
    out_file.write_text(json.dumps({
        "episode_number": ep_num,
        "story_id": story["id"],
        "story_title": story["title"],
        "title": f"{story['title']} — {plan['title']}",
        "segments": segments,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")

    # 8) تحديث الحالة
    state["episode_num"] = ep_num + 1
    state["covered_topics"] = state.get("covered_topics", []) + story.get("key_topics", [])[:2]
    save_state(state)

    # لا نحذف pending هنا — يُحذف بعد نجاح الفيديو فقط
    print(f"✅ JSON: {out_file}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        notify_telegram(f"فشل generate_story: {e}", "error")
        sys.exit(1)
