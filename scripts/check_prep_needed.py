"""
check_prep_needed.py — يقرر إن كانت هناك قصة تحتاج تجهيزاً
يطبع ID القصة (أو سلسلة فارغة).

المنطق:
  1. Bootstrap: إذا القصة الحالية بدون audited → جهّزها.
  2. إذا القصة الحالية على وشك الانتهاء (< 3 أحداث) والقصة التالية غير مجهزة → جهّز التالية.
  3. خلاف ذلك → لا حاجة.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from story_library import load_global, load_story_state, load_library

STORIES_DIR = _REPO_ROOT / "data" / "stories"
MIN_REMAINING_TO_PREP = 3


def main():
    try:
        print("🔍 check_prep_needed: بدء", file=sys.stderr)
        
        gstate = load_global()
        current_id = gstate.get("current_story_id")
        print(f"🔍 current_story_id = {current_id}", file=sys.stderr)
        
        if not current_id:
            print("❌ current_story_id فارغ!", file=sys.stderr)
            print("")
            return

        library = load_library()
        stories = sorted(library["stories"], key=lambda s: s.get("chron_order", 0))
        print(f"🔍 عدد القصص = {len(stories)}", file=sys.stderr)

        current_idx = next(
            (i for i, s in enumerate(stories) if s["id"] == current_id),
            None
        )
        print(f"🔍 current_idx = {current_idx}", file=sys.stderr)
        
        if current_idx is None:
            print(f"❌ القصة '{current_id}' غير موجودة في stories.json!", file=sys.stderr)
            print("")
            return

        # ═══════════════════════════════════════════════════
        # 1. Bootstrap: القصة الحالية بدون audited → جهّزها
        # ═══════════════════════════════════════════════════
        audited_events = STORIES_DIR / current_id / "audited" / "events"
        has_audited = audited_events.exists() and list(audited_events.glob("event_*"))
        print(f"🔍 audited exists = {audited_events.exists()}, events = {bool(has_audited)}", file=sys.stderr)

        if not has_audited:
            print(f"✅ Bootstrap: القصة الحالية '{current_id}' تحتاج تجهيزاً", file=sys.stderr)
            print(current_id)
            return

        # ═══════════════════════════════════════════════════
        # 2. القصة الحالية على وشك الانتهاء؟
        # ═══════════════════════════════════════════════════
        total_events = len(list(audited_events.glob("event_*")))
        sstate = load_story_state(current_id)
        remaining = total_events - sstate["current_event_index"]
        print(f"🔍 remaining events = {remaining}", file=sys.stderr)

        if remaining > MIN_REMAINING_TO_PREP:
            print(f"⏭️ لا حاجة (بقي {remaining} أحداث)", file=sys.stderr)
            print("")
            return

        # ═══════════════════════════════════════════════════
        # 3. القصة التالية — مجهزة أم لا؟
        # ═══════════════════════════════════════════════════
        next_idx = (current_idx + 1) % len(stories)
        next_id = stories[next_idx]["id"]

        next_audited = STORIES_DIR / next_id / "audited" / "events"
        if next_audited.exists() and list(next_audited.glob("event_*")):
            print(f"⏭️ التالية '{next_id}' مجهزة سابقاً", file=sys.stderr)
            print("")
            return

        print(f"✅ القصة التالية '{next_id}' تحتاج تجهيزاً", file=sys.stderr)
        print(next_id)

    except Exception as e:
        print(f"❌ check_prep_needed: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        print("")


if __name__ == "__main__":
    main()
