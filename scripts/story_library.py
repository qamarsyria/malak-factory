"""
story_library.py — إدارة الحالة المركزية (global + per-story)
ملف مساعد نظيف تستخدمه كل السكربتات.

الاستخدام:
    from story_library import (
        load_global, save_global,
        load_story_state, save_story_state,
        get_current_story, get_story_by_id,
        advance_to_next_episode, is_story_complete,
        needs_manual_review,
    )
"""

import json
from pathlib import Path
from datetime import datetime, timezone

_REPO_ROOT = Path(__file__).resolve().parent.parent
STORIES_JSON   = _REPO_ROOT / "data" / "library" / "stories.json"
GLOBAL_STATE   = _REPO_ROOT / "state" / "global.json"
STORIES_DIR    = _REPO_ROOT / "data" / "stories"

# ═══════════════════════════════════════════════════════════
# تحميل/حفظ الحالة المركزية
# ═══════════════════════════════════════════════════════════
def load_global() -> dict:
    """يقرأ state/global.json."""
    if not GLOBAL_STATE.exists():
        raise RuntimeError(f"ملف الحالة غير موجود: {GLOBAL_STATE}")
    return json.loads(GLOBAL_STATE.read_text(encoding="utf-8"))


def save_global(state: dict):
    """يحفظ state/global.json مع timestamp."""
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    GLOBAL_STATE.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ═══════════════════════════════════════════════════════════
# تحميل/حفظ حالة القصة
# ═══════════════════════════════════════════════════════════
def _story_state_file(story_id: str) -> Path:
    return STORIES_DIR / story_id / "state.json"


def load_story_state(story_id: str) -> dict:
    """يقرأ state.json للقصة أو يعيد الحالة الافتراضية."""
    path = _story_state_file(story_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ فشل قراءة state لـ {story_id}: {e}")

    return {
        "story_id": story_id,
        "current_event_index": 0,
        "current_sub_episode": 0,
        "total_episodes_generated": 0,
        "completed": False,
        "last_generated": None,
    }


def save_story_state(story_id: str, state: dict):
    """يحفظ state.json للقصة."""
    path = _story_state_file(story_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["story_id"] = story_id
    state["last_generated"] = datetime.now(timezone.utc).isoformat()
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ═══════════════════════════════════════════════════════════
# قراءة المكتبة
# ═══════════════════════════════════════════════════════════
def load_library() -> dict:
    """يقرأ data/library/stories.json."""
    if not STORIES_JSON.exists():
        raise RuntimeError(f"مكتبة القصص غير موجودة: {STORIES_JSON}")
    return json.loads(STORIES_JSON.read_text(encoding="utf-8"))


def get_all_stories() -> list:
    """يرجع كل القصص مرتبة حسب chron_order."""
    lib = load_library()
    return sorted(lib["stories"], key=lambda s: s.get("chron_order", 0))


def get_story_by_id(story_id: str) -> dict | None:
    """يرجع قصة عبر ID."""
    for story in load_library()["stories"]:
        if story["id"] == story_id:
            return story
    return None


def get_current_story() -> dict:
    """يرجع القصة الحالية (من global.json)."""
    gstate = load_global()
    story = get_story_by_id(gstate["current_story_id"])
    if not story:
        raise RuntimeError(f"القصة الحالية '{gstate['current_story_id']}' غير موجودة في المكتبة")
    return story


# ═══════════════════════════════════════════════════════════
# التقدم والتقدم
# ═══════════════════════════════════════════════════════════
def is_story_complete(story_id: str, total_events: int) -> bool:
    """
    هل القصة اكتملت؟
    - الحالة تكملة (completed = True), أو
    - current_event_index >= total_events
    """
    state = load_story_state(story_id)
    if state.get("completed"):
        return True
    return state["current_event_index"] >= total_events


def advance_to_next_episode(story_id: str):
    """يزيد sub_episode بمقدار 1 ويزيد total_generated."""
    state = load_story_state(story_id)
    state["current_sub_episode"] += 1
    state["total_episodes_generated"] = state.get("total_episodes_generated", 0) + 1
    save_story_state(story_id, state)


def advance_to_next_event(story_id: str):
    """ينتقل للحدث التالي، ويصفّر sub_episode."""
    state = load_story_state(story_id)
    state["current_event_index"] += 1
    state["current_sub_episode"] = 0
    save_story_state(story_id, state)


def mark_story_complete(story_id: str):
    """يعلن القصة مكتملة."""
    state = load_story_state(story_id)
    state["completed"] = True
    save_story_state(story_id, state)
    print(f"✅ القصة '{story_id}' اكتملت")


def advance_to_next_story() -> str:
    """
    ينتقل للقصة التالية (بناءً على chron_order).
    يعيد ID القصة الجديدة.
    """
    gstate = load_global()
    stories = get_all_stories()

    current_idx = gstate.get("current_story_index", 0)
    next_idx = current_idx + 1

    if next_idx >= len(stories):
        # الموسوعة كاملة — دورة جديدة
        print("🎊 اكتملت الموسوعة — دورة جديدة")
        gstate["completed_stories"] = [s["id"] for s in stories]
        gstate["current_story_index"] = 0
        gstate["current_story_id"] = stories[0]["id"]
        gstate["project_cycle"] = gstate.get("project_cycle", 0) + 1
        # إعادة تعيين المراجعة
        gstate["reviewed_eras"] = []
        gstate["reviewed_at_system_version"] = gstate.get("system_version", "1.0")
        next_idx = 0
    else:
        gstate["current_story_index"] = next_idx
        gstate["current_story_id"] = stories[next_idx]["id"]

    # إعادة تعيين حالة القسم
    new_era = stories[next_idx]["era"]
    if gstate.get("current_era") != new_era:
        gstate["current_era"] = new_era
        gstate["current_era_episodes_published"] = 0

    gstate["current_story_phase"] = "collecting"
    save_global(gstate)

    return stories[next_idx]["id"]


# ═══════════════════════════════════════════════════════════
# مراجعة يدوية (أول 3 حلقات من كل قسم)
# ═══════════════════════════════════════════════════════════
def needs_manual_review(era_id: str) -> bool:
    """
    هل هذا القسم يحتاج مراجعة يدوية لأول 3 حلقات؟
    - إذا القسم لم يُراجَع بعد → نعم.
    - إذا النظام تغيّر (system_version مختلف) → نعم.
    """
    gstate = load_global()
    reviewed = gstate.get("reviewed_eras", [])
    if era_id in reviewed:
        return False
    return True


def mark_era_as_reviewed(era_id: str):
    """يعلن أن هذا القسم رُوجع (أول 3 حلقات منه)."""
    gstate = load_global()
    reviewed = set(gstate.get("reviewed_eras", []))
    if era_id not in reviewed:
        reviewed.add(era_id)
        gstate["reviewed_eras"] = sorted(reviewed)
        save_global(gstate)
        print(f"✅ القسم '{era_id}' أصبح مُراجَعاً")


def increment_era_episode_count():
    """يزيد عدّاد حلقات القسم الحالي، وإذا وصل 3 → يُعلن مُراجَعاً."""
    gstate = load_global()
    gstate["current_era_episodes_published"] = gstate.get("current_era_episodes_published", 0) + 1
    era = gstate.get("current_era")
    if era and gstate["current_era_episodes_published"] >= 3:
        reviewed = set(gstate.get("reviewed_eras", []))
        reviewed.add(era)
        gstate["reviewed_eras"] = sorted(reviewed)
        print(f"✅ القسم '{era}' اكتمل مراجعته (3 حلقات)")
    save_global(gstate)


# ═══════════════════════════════════════════════════════════
# إحصائيات
# ═══════════════════════════════════════════════════════════
def get_progress_summary() -> dict:
    """ملخص التقدم الكلي."""
    gstate = load_global()
    stories = get_all_stories()
    return {
        "current_story_id": gstate["current_story_id"],
        "current_story_index": gstate["current_story_index"],
        "current_story_phase": gstate["current_story_phase"],
        "current_era": gstate.get("current_era", ""),
        "total_stories": len(stories),
        "completed_stories": len(gstate.get("completed_stories", [])),
        "total_episodes_published": gstate.get("total_episodes_published", 0),
        "reviewed_eras": gstate.get("reviewed_eras", []),
        "project_cycle": gstate.get("project_cycle", 0),
        "progress_percent": round(
            len(gstate.get("completed_stories", [])) / max(len(stories), 1) * 100, 1
        ),
    }


# ═══════════════════════════════════════════════════════════
# للاختبار
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("🧪 اختبار story_library.py")
    print("=" * 60)

    try:
        gstate = load_global()
        print(f"\n📊 الحالة الحالية:")
        for k, v in get_progress_summary().items():
            print(f"   {k}: {v}")

        print(f"\n📖 القصة الحالية: {get_current_story()['title']}")
        print(f"📚 إجمالي القصص: {len(get_all_stories())}")

        test_id = gstate["current_story_id"]
        sstate = load_story_state(test_id)
        print(f"\n📄 حالة '{test_id}':")
        for k, v in sstate.items():
            print(f"   {k}: {v}")

        print(f"\n🔍 مراجعة يدوية مطلوبة للقسم '{gstate['current_era']}'؟ "
              f"{'نعم' if needs_manual_review(gstate['current_era']) else 'لا'}")

        print("\n✅ الاختبار نجح")

    except Exception as e:
        print(f"❌ فشل: {e}")
