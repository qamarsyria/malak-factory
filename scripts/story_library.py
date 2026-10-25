"""إدارة مكتبة القصص: تحميل، حالة، اختيار القصة التالية."""
import json
from pathlib import Path

LIBRARY_FILE = Path("data/library/stories.json")
STATE_FILE = Path("state/story_state.json")


def load_library() -> list:
    if not LIBRARY_FILE.exists():
        return []
    try:
        data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        return data.get("stories", [])
    except Exception as e:
        print(f"⚠️ فشل قراءة المكتبة: {e}")
        return []


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "current_story_id": None,
        "current_story_index": 0,
        "episode_num": 1,
        "covered_topics": [],
        "completed_stories": [],
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_current_story(library: list, state: dict) -> dict:
    """يعيد القصة الحالية، أو ينتقل للتالية تلقائياً."""
    if not library:
        raise RuntimeError("المكتبة فارغة")

    completed = set(state.get("completed_stories", []))
    idx = state.get("current_story_index", 0)

    while idx < len(library) and library[idx]["id"] in completed:
        idx += 1

    if idx >= len(library):
        # اكتملت كل القصص — أعد البدء من الأولى بجولة جديدة
        completed.clear()
        idx = 0
        state["completed_stories"] = []
        state["cycle"] = state.get("cycle", 0) + 1

    state["current_story_index"] = idx
    state["current_story_id"] = library[idx]["id"]
    story = library[idx]

    # إذا كانت القصة جديدة، أعد ضبط عداد الحلقات
    if state.get("episode_num", 1) > story.get("target_episodes", 40) \
            or state.get("current_story_id") != story["id"]:
        state.setdefault("episode_num", 1)

    return story


def should_advance_story(story: dict, state: dict) -> bool:
    """هل يجب الانتقال للقصة التالية؟"""
    target = story.get("target_episodes", 40)
    return state.get("episode_num", 1) > target


def advance_story(state: dict, current_story: dict):
    """ينقل للقصة التالية ويُسجّل الحالية كمكتملة."""
    completed = set(state.get("completed_stories", []))
    completed.add(current_story["id"])
    state["completed_stories"] = list(completed)
    state["current_story_index"] = state.get("current_story_index", 0) + 1
    state["episode_num"] = 1
    state["covered_topics"] = []


def next_episode_metadata() -> dict:
    """تحديث الحالة بعد حلقة ناجحة."""
    library = load_library()
    state = load_state()
    story = get_current_story(library, state)
    state["episode_num"] = state.get("episode_num", 1) + 1
    save_state(state)
    return {"story": story, "state": state}
