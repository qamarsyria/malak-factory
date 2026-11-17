"""
finalize_episode.py — يُحدّث الحالة بعد نجاح كامل خط الإنتاج
(بعد رفع تلغرام). إذا فشل شيء قبل، لا يُستدعى.

الاستخدام:
    python finalize_episode.py <path_to_episode_json>
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from story_library import (
    load_global, save_global,
    load_story_state, save_story_state,
    get_story_by_id,
)


def main():
    if len(sys.argv) < 2:
        print("الاستخدام: finalize_episode.py <episode.json>")
        sys.exit(1)

    ep_file = Path(sys.argv[1])
    if not ep_file.exists():
        print(f"❌ غير موجود: {ep_file}")
        sys.exit(1)

    ep = json.loads(ep_file.read_text(encoding="utf-8"))
    story_id = ep.get("story_id")
    ep_num = ep.get("episode_number", 1)

    if not story_id:
        print("❌ story_id مفقود في JSON")
        sys.exit(1)

    story = get_story_by_id(story_id)
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة")
        sys.exit(1)

    # 1. حالة القصة
    sstate = load_story_state(story_id)
    sstate["current_sub_episode"] += 1
    sstate["total_episodes_generated"] = ep_num
    save_story_state(story_id, sstate)
    print(f"✅ story_state: sub_episode={sstate['current_sub_episode']}, total={ep_num}")

    # 2. الحالة المركزية
    gstate = load_global()
    gstate["total_episodes_published"] = gstate.get("total_episodes_published", 0) + 1
    gstate["current_era_episodes_published"] = gstate.get("current_era_episodes_published", 0) + 1

    # 3. مراجعة القسم (بعد 3 حلقات)
    if gstate["current_era_episodes_published"] >= 3:
        reviewed = set(gstate.get("reviewed_eras", []))
        reviewed.add(story["era"])
        gstate["reviewed_eras"] = sorted(reviewed)
        print(f"✅ القسم '{story['era']}' أصبح مُراجَعاً")

    save_global(gstate)
    print(f"✅ global: total_published={gstate['total_episodes_published']}")


if __name__ == "__main__":
    main()
