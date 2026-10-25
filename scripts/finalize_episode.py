"""يُحدّث الحالة بعد نجاح كامل خط الإنتاج (بعد رفع تلغرام)."""
import sys
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from story_library import load_state, save_state  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("❌ الاستخدام: finalize_episode.py <path_to_episode_json>")
        sys.exit(1)

    ep_file = Path(sys.argv[1])
    if not ep_file.exists():
        print(f"⚠️ JSON غير موجود: {ep_file}")
        sys.exit(1)

    ep = json.loads(ep_file.read_text(encoding="utf-8"))
    ep_num = ep.get("episode_number", 1)
    story_id = ep.get("story_id", "")

    state = load_state()
    current = state.get("current_story_id")
    if current and current != story_id:
        print(f"⚠️ عدم تطابق: {story_id} ≠ {current} — لا تحديث")
        sys.exit(0)

    state["episode_num"] = ep_num + 1
    state["current_story_id"] = story_id
    save_state(state)
    print(f"✅ تحديث الحالة: {story_id} → الحلقة {ep_num + 1}")

    pending = _REPO_ROOT / "state/pending" / f"{story_id}-{ep_num:02d}.json"
    if pending.exists():
        pending.unlink()
        print(f"🗑️ حذف pending: {pending.name}")


if __name__ == "__main__":
    main()
