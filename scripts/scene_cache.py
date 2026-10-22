"""تخزين مؤقت لاختيارات المخرج السينمائي (صور + أصوات) لكل جملة."""
import json
import hashlib
from pathlib import Path

CACHE_FILE = "out/director_cache.json"

_cache = {}
_loaded = False


def _load():
    global _cache, _loaded
    if _loaded:
        return
    try:
        if Path(CACHE_FILE).exists():
            _cache = json.loads(Path(CACHE_FILE).read_text(encoding="utf-8"))
    except Exception:
        _cache = {}
    _loaded = True


def _save():
    try:
        Path(CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(CACHE_FILE).write_text(
            json.dumps(_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def get_cached(text: str):
    """يرجع الاختيار المخزن أو None."""
    _load()
    key = hashlib.md5(text.strip()[:200].encode("utf-8")).hexdigest()
    return _cache.get(key)


def set_cache(text: str, visual: str, sfx: str):
    """يحفظ الاختيار في الكاش."""
    _load()
    key = hashlib.md5(text.strip()[:200].encode("utf-8")).hexdigest()
    _cache[key] = {"visual": visual, "sfx": sfx}
    _save()
