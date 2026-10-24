def main():
    state = load_state()
    story_name = STORIES_ORDER[state["story_index"]]
    ep_num = state["episode_num"]
    
    if ep_num > MAX_EPISODES_PER_STORY:
        print(f"🎉 انتهت قصة {story_name}.")
        state["story_index"] += 1
        state["episode_num"] = 1
        save_state(state)
        sys.exit(0)

    print(f"📖 توليد: {story_name} - الحلقة {ep_num}")

    # الخطوة 1: توليد خطة الحلقة (عنوان + ملخص غني)
    plan_prompt = f"""أنت كاتب سيناريو إسلامي.
المطلوب: ابتكر فكرة للحلقة رقم {ep_num} من قصة {story_name}.
اعطني عنواناً درامياً جذاباً، وملخصاً موسعاً (لا يقل عن 300 كلمة).
أعد النتيجة بصيغة JSON:
{{"title": "عنوان الحلقة", "summary": "ملخص مفصل جداً..."}}"""
    
    plan = call_llm(plan_prompt)
    if not plan or "title" not in plan:
        print("❌ فشل توليد فكرة الحلقة.")
        sys.exit(1)
    
    print(f"   📜 العنوان: {plan['title']}")

    # الخطوة 2: توليد السرد فقط (بدون JSON معقد)
    script_prompt = f"""أنت كاتب سيناريو إسلامي.
اكتب نصاً سردياً درامياً مفصلاً (1500 كلمة) لهذه الحلقة.
القصة: {story_name}
العنوان: {plan['title']}
الملخص: {plan['summary']}

اكتب النص كاملاً. لا تختصر. لا تضع أي رموز أو أقواس.
أعد النص فقط."""
    
    script_text = call_llm(script_prompt, is_json=False, timeout=120, max_tokens=8192)
    
    if not script_text or len(script_text.split()) < 800:
        print(f"❌ فشل توليد السرد أو النص قصير جداً ({len(script_text.split()) if script_text else 0} كلمة).")
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (PENDING_DIR / f"pending-{story_name}-{ep_num}.json").write_text(json.dumps({"title": plan["title"], "summary": plan["summary"]}), encoding="utf-8")
        sys.exit(0)

    # الخطوة 3: بناء الـ Segments برمجياً (سرد + آيات)
    # نقوم بتقسيم النص إلى فقرات، ونضيف الآيات بعد كل فقرة
    paragraphs = [p.strip() for p in script_text.split('\n') if p.strip()]
    
    # جلب الآيات المناسبة للحلقة (نستخدم Gemini لاستخراج الآيات فقط)
    ayah_prompt = f"""استخرج 5 آيات قرآنية تدعم هذا النص:
{script_text[:2000]}
أعد JSON: {{"ayahs": [{{"surah": 3, "ayah": 35}}]}}"""
    
    ayah_res = call_llm(ayah_prompt)
    ayahs = ayah_res.get("ayahs", []) if ayah_res else []

    verified = []
    for i, p in enumerate(paragraphs):
        verified.append({"type": "narration", "text": p})
        if i < len(ayahs):
            verified.append({"type": "quran", "surah": ayahs[i]["surah"], "ayah_numbers": [ayahs[i]["ayah"]]})

    # إضافة Outro و Teaser
    outro = random.choice(OUTRO_TEXTS)
    verified.append({"type": "outro", "text": outro["text"], "emotion_hint": outro["voice_style"], "visual": "mosque", "sfx": ""})
    
    next_title = f"الحلقة {ep_num + 1}" if ep_num < MAX_EPISODES_PER_STORY else "الحلقة القادمة"
    verified.append({"type": "teaser", "text": f"وهل تعلم ما حدث بعد ذلك؟ الحلقة القادمة: {next_title}.", "emotion_hint": "(mysterious)", "next_episode_title": f"{story_name} - {next_title}", "next_episode_source": story_name, "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    # الحفظ
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{story_name}-{ep_num:02d}.json"
    out_file.write_text(json.dumps({"episode_number": ep_num, "title": f"{story_name} - {plan['title']} - الحلقة {ep_num}", "segments": verified}, ensure_ascii=False, indent=2), encoding="utf-8")
    
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    state["episode_num"] += 1
    save_state(state)
    print(f"✅ نجح: {out_file}")
