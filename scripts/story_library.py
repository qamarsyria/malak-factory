"""إدارة مكتبة القصص والحالة."""
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_FILE = _REPO_ROOT / "data/library/stories.json"
STATE_FILE = _REPO_ROOT / "state/story_state.json"


def load_library() -> list:
    if not LIBRARY_FILE.exists():
        return []
    try:
        return json.loads(LIBRARY_FILE.read_text(encoding="utf-8")).get("stories", [])
    except Exception as e:
        print(f"⚠️ فشل قراءة المكتبة: {e}", flush=True)
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
        "cycle": 0,
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_current_story(library: list, state: dict) -> dict:
    """يعيد القصة الحالية، متقدماً تلقائياً عند الحاجة."""
    if not library:
        raise RuntimeError("المكتبة فارغة")

    while True:
        completed = set(state.get("completed_stories", []))
        idx = state.get("current_story_index", 0)

        while idx < len(library) and library[idx]["id"] in completed:
            idx += 1

        if idx >= len(library):
            state["cycle"] = state.get("cycle", 0) + 1
            state["completed_stories"] = []
            state["current_story_index"] = 0
            continue

        story = library[idx]
        state["current_story_index"] = idx

        if state.get("episode_num", 1) > story.get("target_episodes", 40):
            completed.add(story["id"])
            state["completed_stories"] = list(completed)
            state["current_story_index"] = idx + 1
            state["episode_num"] = 1
            continue

        if state.get("current_story_id") != story["id"]:
            state["episode_num"] = 1
            state["covered_topics"] = []

        state["current_story_id"] = story["id"]
        return story
