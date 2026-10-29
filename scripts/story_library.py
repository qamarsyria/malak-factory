"""إدارة مكتبة القصص والحالة — مُحصَّن ضد الأخطاء."""
import json
from pathlib import Path
from datetime import datetime, timezone

_REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_FILE = _REPO_ROOT / "data" / "library" / "stories.json"
STATE_FILE = _REPO_ROOT / "state" / "story_state.json"


def load_library() -> list:
    if not LIBRARY_FILE.exists():
        print(f"⚠️ مكتبة القصص غير موجودة: {LIBRARY_FILE}")
        return []
    try:
        data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        stories = data.get("stories", [])
        print(f"📚 مكتبة القصص: {len(stories)} قصة")
        return stories
    except Exception as e:
        print(f"⚠️ فشل قراءة المكتبة: {e}")
        return []


def _default_state() -> dict:
    return {
        "current_story_id": None,
        "current_story_index": 0,
        "episode_num": 1,
        "covered_topics": [],
        "completed_stories": [],
        "cycle": 0,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            # ضمان وجود كل المفاتيح
            for k, v in _default_state().items():
                state.setdefault(k, v)
            return state
        except Exception as e:
            print(f"⚠️ فشل قراءة الحالة: {e}")
    return _default_state()


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_current_story(library: list, state: dict) -> dict:
    """
    يعيد القصة الحالية بأمان.
    - يطابق current_story_id مع المكتبة.
    - إذا لم يجد، يبحث عن أول قصة غير مكتملة.
    - لا يُعيد التهيئة بصمت أبداً (يطبع تحذيراً).
    """
    if not library:
        raise RuntimeError("المكتبة فارغة — لا يمكن اختيار قصة")

    # 1. مطابقة بـ id
    current_id = state.get("current_story_id")
    if current_id:
        for idx, story in enumerate(library):
            if story.get("id") == current_id:
                # تحقق من أن الحلقة لم تتجاوز target
                target = story.get("target_episodes", 40)
                if state.get("episode_num", 1) > target:
                    print(
                        f"✅ اكتملت قصة {story['title']} "
                        f"({state['episode_num'] - 1}/{target} حلقة) — الانتقال للتالية"
                    )
                    completed = set(state.get("completed_stories", []))
                    completed.add(current_id)
                    state["completed_stories"] = list(completed)
                    state["current_story_id"] = None
                    state["episode_num"] = 1
                    state["covered_topics"] = []
                    state["current_story_index"] = idx + 1
                    return _pick_next_incomplete(library, state)

                state["current_story_index"] = idx
                print(
                    f"📖 القصة: {story['title']} — "
                    f"الحلقة {state.get('episode_num', 1)}/{target}"
                )
                return story

        # ← لم نجد القصة في المكتبة!
        print(
            f"⚠️ القصة '{current_id}' غير موجودة في المكتبة. "
            f"المكتبة تحتوي على: {[s['id'] for s in library]}"
        )
        # لا نُعيد التهيئة بصمت — نبحث عن قصة بديلة

    # 2. البحث من current_story_index
    start_idx = state.get("current_story_index", 0)
    if not (0 <= start_idx < len(library)):
        print(f"⚠️ current_story_index={start_idx} خارج النطاق — البدء من 0")
        start_idx = 0

    return _pick_next_incomplete(library, state, start_idx)


def _pick_next_incomplete(library: list, state: dict, start_idx: int = 0) -> dict:
    """اختيار أول قصة غير مكتملة."""
    completed = set(state.get("completed_stories", []))
    n = len(library)

    for offset in range(n):
        idx = (start_idx + offset) % n
        story = library[idx]
        if story.get("id") in completed:
            continue

        state["current_story_id"] = story["id"]
        state["current_story_index"] = idx
        target = story.get("target_episodes", 40)
        print(
            f"📖 القصة: {story['title']} — "
            f"الحلقة {state.get('episode_num', 1)}/{target}"
        )
        return story

    # كل القصص مكتملة → دورة جديدة
    print("🔄 جميع القصص مكتملة — بدء دورة جديدة")
    state["completed_stories"] = []
    state["cycle"] = state.get("cycle", 0) + 1
    state["current_story_id"] = library[0]["id"]
    state["current_story_index"] = 0
    state["episode_num"] = 1
    state["covered_topics"] = []
    return library[0]


def record_topic(state: dict, topic: str):
    """تسجيل موضوع مُغطى (لتجنب التكرار)."""
    if not topic:
        return
    covered = state.get("covered_topics", [])
    if topic not in covered:
        covered.append(topic)
        state["covered_topics"] = covered


def is_topic_covered(state: dict, topic: str) -> bool:
    """هل تم تغطية هذا الموضوع سابقاً؟"""
    return topic in state.get("covered_topics", [])
