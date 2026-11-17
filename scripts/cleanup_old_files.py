"""
cleanup_old_files.py — تنظيف بعد نجاح الرفع على تلغرام

⚠️ يُستدعى فقط بعد نجاح الرفع.
⚠️ لا يحذف المصادر الأصلية (collected/audited/state).

✅ يحذف:
  - data/stories/{story}/generated/{story}-{ep}.json   (السكريبت المنشور)
  - out/audio_cache/{story}-{ep}/                     (كاش الصوت)
  - out/*{story}-{ep}*.mp3                            (الملفات الصوتية)
  - out/*{story}-{ep}_meta.json                       (الميتاداتا)
  - videos/*{story}-{ep}*.mp4                         (الفيديوهات)
  - temp_chunks/, rvc/temp_chunks/                    (مخلفات RVC)

➕ تنظيف احتياطي:
  - كاش صوت أقدم من 7 أيام (يتنظف تلقائياً كل تشغيل)
  - كاش مشاهد أقدم من 3 أيام

الاستخدام:
    python cleanup_old_files.py --episode yusuf-01
    python cleanup_old_files.py --episode yusuf-01 --dry-run
    python cleanup_old_files.py --old-cache-only
"""

import os
import sys
import shutil
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ═══════════════════════════════════════════════════════════
# المسارات
# ═══════════════════════════════════════════════════════════
STORIES_DIR       = _REPO_ROOT / "data" / "stories"
OUT_DIR           = _REPO_ROOT / "out"
AUDIO_CACHE       = OUT_DIR / "audio_cache"
SCENE_CACHE       = OUT_DIR / "scene_cache"
VIDEOS_DIR        = _REPO_ROOT / "videos"
TEMP_CHUNKS       = _REPO_ROOT / "temp_chunks"
RVC_TEMP          = _REPO_ROOT / "rvc" / "temp_chunks"

# ═══════════════════════════════════════════════════════════
# حدود الكاش الاحتياطي
# ═══════════════════════════════════════════════════════════
AUDIO_CACHE_MAX_DAYS = 7
SCENE_CACHE_MAX_DAYS = 3

# ═══════════════════════════════════════════════════════════
# مساعدات
# ═══════════════════════════════════════════════════════════
def size_of(p: Path) -> float:
    """يحسب حجم ملف/مجلد بالميغابايت."""
    if p.is_file():
        return p.stat().st_size / 1024 / 1024
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1024 / 1024
    return 0.0


def safe_remove(p: Path, dry_run: bool = False) -> float:
    """يحذف ملفاً/مجلداً بأمان، ويعيد الحجم الموفّر (MB)."""
    if not p.exists():
        return 0.0

    size = size_of(p)
    if dry_run:
        print(f"   [dry-run] سيُحذف: {p.relative_to(_REPO_ROOT)} ({size:.2f} MB)")
        return size

    try:
        if p.is_file():
            p.unlink()
        else:
            shutil.rmtree(p)
        print(f"   🗑️ {p.relative_to(_REPO_ROOT)} ({size:.2f} MB)")
        return size
    except Exception as e:
        print(f"   ⚠️ فشل حذف {p.name}: {e}")
        return 0.0


def glob_remove(pattern: Path, dry_run: bool = False) -> float:
    """يحذف كل الملفات المطابقة لـ glob."""
    total = 0.0
    for f in pattern.parent.glob(pattern.name):
        total += safe_remove(f, dry_run)
    return total


# ═══════════════════════════════════════════════════════════
# التنظيف الخاص بحلقة
# ═══════════════════════════════════════════════════════════
def cleanup_episode(episode_name: str, dry_run: bool = False) -> float:
    """
    ينظف كل ملفات حلقة محددة.
    episode_name: مثل "yusuf-01" (بدون امتداد)
    """
    print(f"\n🧹 تنظيف الحلقة: {episode_name}")

    # استخرج story_id من episode_name (مثل "yusuf-01" → "yusuf")
    story_id = episode_name.rsplit("-", 1)[0] if "-" in episode_name else episode_name

    total = 0.0

    # 1. generated/{story}-{ep}.json
    gen_file = STORIES_DIR / story_id / "generated" / f"{episode_name}.json"
    total += safe_remove(gen_file, dry_run)

    # 2. audio_cache/{episode_name}/
    cache_dir = AUDIO_CACHE / episode_name
    total += safe_remove(cache_dir, dry_run)

    # 3. out/*{episode_name}*.mp3
    for pattern in OUT_DIR.glob(f"*{episode_name}*.mp3"):
        total += safe_remove(pattern, dry_run)

    # 4. out/*{episode_name}_meta.json
    for pattern in OUT_DIR.glob(f"*{episode_name}*_meta.json"):
        total += safe_remove(pattern, dry_run)

    # 5. videos/*{episode_name}*.mp4
    if VIDEOS_DIR.exists():
        for pattern in VIDEOS_DIR.glob(f"*{episode_name}*.mp4"):
            total += safe_remove(pattern, dry_run)

    # 6. temp_chunks/ (كامل — لأن الحلقة انتهت)
    if TEMP_CHUNKS.exists():
        total += safe_remove(TEMP_CHUNKS, dry_run)

    # 7. rvc/temp_chunks/
    if RVC_TEMP.exists():
        total += safe_remove(RVC_TEMP, dry_run)

    return total


# ═══════════════════════════════════════════════════════════
# التنظيف الاحتياطي (كاش قديم)
# ═══════════════════════════════════════════════════════════
def cleanup_old_cache(audio_days: int = AUDIO_CACHE_MAX_DAYS,
                      scene_days: int = SCENE_CACHE_MAX_DAYS,
                      dry_run: bool = False) -> float:
    """ينظف الكاش الأقدم من الحد المحدد."""
    print(f"\n🗓️ تنظيف كاش قديم (صوت > {audio_days} أيام، مشاهد > {scene_days} أيام)")

    now = datetime.now(timezone.utc)
    total = 0.0

    # audio_cache
    if AUDIO_CACHE.exists():
        for item in AUDIO_CACHE.iterdir():
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                age = (now - mtime).days
                if age > audio_days:
                    total += safe_remove(item, dry_run)
            except Exception:
                pass

    # scene_cache
    if SCENE_CACHE.exists():
        for item in SCENE_CACHE.iterdir():
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                age = (now - mtime).days
                if age > scene_days:
                    total += safe_remove(item, dry_run)
            except Exception:
                pass

    return total


# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="تنظيف بعد الرفع")
    parser.add_argument("--episode", type=str, help="اسم الحلقة (مثل yusuf-01)")
    parser.add_argument("--old-cache-only", action="store_true",
                        help="تنظيف الكاش القديم فقط (بلا حذف حلقة)")
    parser.add_argument("--dry-run", action="store_true",
                        help="لا يحذف — يعرض فقط ما سيُحذف")
    args = parser.parse_args()

    print("=" * 60)
    print("🧹 cleanup_old_files.py")
    if args.dry_run:
        print("   ⚠️ DRY RUN — لن يحذف شيء")
    print("=" * 60)

    total_freed = 0.0

    # 1. تنظيف الحلقة (إن مُرّرت)
    if args.episode:
        total_freed += cleanup_episode(args.episode, dry_run=args.dry_run)

    # 2. تنظيف كاش قديم (دائماً — بلا --old-cache-only أو معه)
    if args.old_cache_only or not args.episode:
        total_freed += cleanup_old_cache(dry_run=args.dry_run)
    elif args.episode:
        # حتى لو نظفنا الحلقة، نمر على الكاش القديم أيضاً
        total_freed += cleanup_old_cache(dry_run=args.dry_run)

    # 3. تقرير
    print("\n" + "=" * 60)
    if args.dry_run:
        print(f"📊 [DRY RUN] سيُوفّر: {total_freed:.2f} MB")
    else:
        print(f"✅ اكتمل التنظيف — وُفّر {total_freed:.2f} MB")
    print("=" * 60)

    # 4. تحذير إذا تجاوز الحجم
    if OUT_DIR.exists() and not args.dry_run:
        out_size = size_of(OUT_DIR)
        if out_size > 500:
            print(f"⚠️ تنبيه: out/ حجمه {out_size:.0f} MB (> 500 MB)")
        if out_size > 1000:
            print(f"🚨 تحذير: out/ حجمه {out_size:.0f} MB (> 1 GB) — تنظيف عاجل مطلوب")


if __name__ == "__main__":
    main()
