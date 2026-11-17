"""
check_prep_needed.py — يقرر إن كانت القصة التالية تحتاج تجهيزاً
يطبع ID القصة أو لا شيء.

المنطق:
  1. القصة الحالية قرب النهاية؟ (< 3 أحداث متبقية)
  2. القصة التالية موجودة لكن غير مجهّزة (collected/audited مفقودان)؟
  → نعم لكليهما: اطبع ID القصة التالية
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from story_library import load_global, load_story_state, load_library

STORIES_DIR = _REPO_ROOT / "data" / "stories"


def main():
    try:
        gstate = load_global()
        current_id = gstate["current_story_id"]

        library = load_library()
        stories = sorted(library["stories"], key=lambda s: s.get("chron_order", 0))

        current_idx = next(
            (i for i, s in enumerate(stories) if s["id"] == current_id),
            None
        )
        if current_idx is None:
            print("")
            return

        # عدد الأحداث المتبقية
        audited_events = STORIES_DIR / current_id / "audited" / "events"
        if not audited_events.exists():
            print("")
            return

        total_events = len(list(audited_events.glob("event_*")))
        sstate = load_story_state(current_id)
        remaining = total_events - sstate["current_event_index"]

        if remaining > 3:
            print("")  # لا حاجة
            return

        # القصة التالية
        next_idx = (current_idx + 1) % len(stories)
        next_id = stories[next_idx]["id"]

        # هل هي مجهزة؟
        next_audited = STORIES_DIR / next_id / "audited"
        if next_audited.exists() and (next_audited / "events").exists():
            events_list = list((next_audited / "events").glob("event_*"))
            if events_list:
                print("")  # مجهزة سابقاً
                return

        print(next_id)

    except Exception as e:
        print(f"", file=sys.stderr)
        print("", file=sys.stdout)


if __name__ == "__main__":
    main()
