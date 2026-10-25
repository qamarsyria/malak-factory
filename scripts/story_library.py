"""إدارة مكتبة القصص والحالة."""
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_FILE = _REPO_ROOT / "data/library/stories.json"
STATE_FILE = _REPO_ROOT / "state/story_state.json"

def load_library() -> list:
    if not LIBRARY_FILE.exists(): return []
    try: return json.loads(LIBRARY_FILE.read_text(encoding="utf-8")).get("stories", [])
    except Exception: return []

def load_state() -> dict:
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"current_story_id": None, "current_story_index": 0, "episode_num": 1, "covered_topics": [], "completed_stories": [], "cycle": 0}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def get_current_story(library: list, state: dict) -> dict:
    if not library: raise RuntimeError("المكتبة فارغة")
    
    current_id = state.get("current_story_id")
    if current_id:
        for idx, s in enumerate(library):
            if s["id"] == current_id:
                if state.get("episode_num", 1) > s.get("target_episodes", 40):
                    completed = set(state.get("completed_stories", []))
                    completed.add(s["id"])
                    state["completed_stories"] = list(completed)
                    state["current_story_index"] = idx + 1
                    state["episode_num"] = 1
                    state["current_story_id"] = None
                    break
                state["current_story_index"] = idx
                return s

    idx = state.get("current_story_index", 0)
    completed = set(state.get("completed_stories", []))
    while idx < len(library):
        story = library[idx]
        if story["id"] not in completed:
            if state.get("episode_num", 1) > story.get("target_episodes", 40):
                completed.add(story["id"])
                state["completed_stories"] = list(completed)
                idx += 1
                state["episode_num"] = 1
                continue
            state["current_story_id"] = story["id"]
            state["current_story_index"] = idx
            return story
        idx += 1

    state["completed_stories"] = []
    state["current_story_index"] = 0
    state["episode_num"] = 1
    story = library[0]
    state["current_story_id"] = story["id"]
    return story
