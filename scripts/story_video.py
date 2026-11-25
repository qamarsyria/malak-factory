"""
story_video.py — المرحلة 5: بناء الفيديو الطولي (720×1280)
- يدعم وضع الأنيميشن/الواقعية السينمائية (CINEMA_ANIME_MODE=1) لمقاطع السرد والملخص والتشويق.
- يفعّل نمط الدفاتر والبطاقات الإسلامية المطرّزة (islamic_cards.py) لمقاطع الحديث والفقه والأذكار والتشجيع (CTA/Outro).
- يحافظ كلياً على الرسم العثماني (AmiriQuran-Regular + MaxFilter Halo + تباعد 2.35×) وتوافق اليوميات (daily_*).
"""

import io
import os
import re
import sys
import json
import gzip
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, features
import arabic_reshaper

try:
    from bidi.algorithm import get_display as bidi_display
except ImportError:
    from bidi import get_display as bidi_display

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PIXABAY_KEY, BG_VIDEOS_DIR, BG_IMAGES_DIR
from utilities import cleanup_dir
from filters_loader import is_safe_tag, is_safe_query, get_allowed_keywords

try:
    from cinema_engine import (
        CINEMA_AVAILABLE,
        plan_episode_shots,
        prepare_and_render_episode_chunks,
        build_halal_ambient_mix,
    )
except Exception as _ce:
    CINEMA_AVAILABLE = False
    print(f"ℹ️ الوضع الكلاسيكي للخلفيات (cinema_engine غير متاح: {_ce})", flush=True)

try:
    from islamic_cards import render_islamic_card_slides
    CARDS_AVAILABLE = True
except Exception as _ice:
    CARDS_AVAILABLE = False
    print(f"ℹ️ بطاقات النصوص المطرّزة غير متاحة: {_ice}", flush=True)

CARD_SEG_TYPES = {"cta", "outro", "hadith", "fiqh", "adhkar"}

HAS_RAQM = features.check("raqm")
print(f"🔤 محرك الخط العربي: {'RAQM (مباشر)' if HAS_RAQM else 'Reshaper + Bidi (احتياطي)'}", flush=True)

DIMENSIONS = {
    "yt":     {"w": 720, "h": 1280},
    "social": {"w": 720, "h": 1280},
}

VIDEO_FPS = 24

ENCODE_ARGS = [
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "18",
    "-pix_fmt", "yuv420p",
]

FINAL_ENCODE_ARGS = [
    "-c:v", "libx264",
    "-preset", "slow",
    "-crf", "27",
    "-maxrate", "340k",
    "-bufsize", "680k",
    "-profile:v", "high",
    "-level:v", "4.0",
    "-g", str(VIDEO_FPS * 4),
    "-bf", "3",
    "-aq-mode", "3",
    "-pix_fmt", "yuv420p",
]

AYAH_LIBRARY = _REPO_ROOT / "data" / "libraries" / "ayah-library.json.gz"
AYAH_LOOKUP: dict[str, str] = {}
SURAH_NAME_LOOKUP: dict[int, str] = {}
UTHMANI_SURAH_CACHE: dict[int, dict[int, str]] = {}
_UTHMANI_CACHE_DIR = Path(tempfile.gettempdir()) / "quran_uthmani_cache"


def load_ayahs():
    global AYAH_LOOKUP, SURAH_NAME_LOOKUP
    if AYAH_LOOKUP or not AYAH_LIBRARY.exists():
        return
    try:
        with gzip.open(AYAH_LIBRARY, "rt", encoding="utf-8") as f:
            ayahs = json.load(f)
        for a in ayahs:
            s = int(a.get("surah") or a.get("surah_number") or 0)
            v = int(a.get("ayah") or a.get("ayah_number") or 0)
            txt = a.get("text") or a.get("arabic_text") or ""
            sname = str(a.get("surah_name", "")).replace("سورة ", "").replace("سُورَةُ ", "").strip()
            if s and v and txt:
                AYAH_LOOKUP[f"{s}:{v}"] = txt
                if sname and s not in SURAH_NAME_LOOKUP:
                    SURAH_NAME_LOOKUP[s] = sname
        print(f"✅ تم تحميل {len(AYAH_LOOKUP)} آية احتياطية من المكتبة المحلية.")
    except Exception as e:
        print(f"⚠️ تعذر تحميل مكتبة الآيات: {e}")


def _fetch_uthmani_surah(surah: int) -> dict[int, str]:
    if surah in UTHMANI_SURAH_CACHE:
        return UTHMANI_SURAH_CACHE[surah]

    _UTHMANI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _UTHMANI_CACHE_DIR / f"surah_{surah}.json"
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            parsed = {int(k): v for k, v in raw.items() if str(v).strip()}
            if parsed:
                UTHMANI_SURAH_CACHE[surah] = parsed
                return parsed
        except Exception:
            pass

    surah_map: dict[int, str] = {}

    try:
        r = requests.get(
            f"https://api.quran.com/api/v4/quran/verses/uthmani?chapter_number={surah}",
            timeout=15,
        )
        if r.status_code == 200:
            for item in r.json().get("verses", []):
                vk = str(item.get("verse_key", ""))
                txt = (item.get("text_uthmani") or "").strip()
                if ":" in vk and txt:
                    _, a_str = vk.split(":", 1)
                    if a_str.isdigit():
                        surah_map[int(a_str)] = txt
    except Exception:
        pass

    if not surah_map:
        try:
            r = requests.get(f"https://api.alquran.cloud/v1/surah/{surah}/quran-uthmani", timeout=15)
            if r.status_code == 200:
                data = r.json().get("data", {})
                sname = str(data.get("name", "")).replace("سورة ", "").replace("سُورَةُ ", "").strip()
                if sname and surah not in SURAH_NAME_LOOKUP:
                    SURAH_NAME_LOOKUP[surah] = sname
                for item in data.get("ayahs", []):
                    a_num = int(item.get("numberInSurah", 0))
                    txt = (item.get("text") or "").strip()
                    if a_num and txt:
                        surah_map[a_num] = txt
        except Exception:
            pass

    if surah_map:
        UTHMANI_SURAH_CACHE[surah] = surah_map
        try:
            cache_file.write_text(json.dumps(surah_map, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    return surah_map


def get_ayah_text_safe(surah: int, ayah: int) -> str:
    if not surah or not ayah:
        return ""
    surah_map = _fetch_uthmani_surah(surah)
    if ayah in surah_map:
        return surah_map[ayah]
    return AYAH_LOOKUP.get(f"{surah}:{ayah}", "")


def find_arabic_font() -> str:
    candidates = [
        "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/hosny-amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    try:
        r = subprocess.run(["fc-list", ":lang=ar", "--format=%{file}\n"], capture_output=True, text=True)
        files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
        for f in files:
            if "Amiri" in f and "Bold" in f:
                return f
        if files:
            return files[0]
    except Exception:
        pass
    raise RuntimeError("❌ لم يُعثر على أي خط عربي")


def find_quran_font() -> str:
    font_cache_dir = Path(tempfile.gettempdir()) / "quran_fonts_cache"
    font_cache_dir.mkdir(parents=True, exist_ok=True)
    cached_amiri_quran = font_cache_dir / "AmiriQuran-Regular.ttf"

    if cached_amiri_quran.exists() and cached_amiri_quran.stat().st_size > 50000:
        return str(cached_amiri_quran)

    urls = [
        "https://cdn.jsdelivr.net/gh/mohammed-2-5/islamic-library-data@master/fonts/AmiriQuran-Regular.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/amiriquran/AmiriQuran-Regular.ttf",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200 and len(r.content) > 50000:
                cached_amiri_quran.write_bytes(r.content)
                return str(cached_amiri_quran)
        except Exception:
            continue

    local_candidates = [
        "/usr/share/fonts/opentype/fonts-hosny-amiri/AmiriQuran.ttf",
        "/usr/share/fonts/truetype/hosny-amiri/AmiriQuran.ttf",
        "/usr/share/fonts/truetype/amiri/AmiriQuran.ttf",
    ]
    for c in local_candidates:
        if os.path.exists(c):
            return c
    try:
        r = subprocess.run(["fc-list", ":lang=ar", "--format=%{file}\n"], capture_output=True, text=True)
        for f in r.stdout.splitlines():
            if "AmiriQuran" in f and os.path.exists(f.strip()):
                return f.strip()
    except Exception:
        pass
    return FONT_PATH


FONT_PATH = find_arabic_font()
QURAN_FONT_PATH = find_quran_font()
print(f"✅ خط السرد: {FONT_PATH}", flush=True)
print(f"✅ خط القرآن: {QURAN_FONT_PATH}", flush=True)


def create_bokeh_layer(W: int, H: int) -> Image.Image:
    bw, bh = W + 400, H + 400
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _ in range(70):
        x, y = random.randint(0, bw), random.randint(0, bh)
        r = random.randint(18, 65)
        alpha = random.randint(55, 165)
        color = random.choice([
            (255, 215, 0),
            (255, 230, 150),
            (212, 175, 55),
            (255, 200, 70),
        ])
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(*color, alpha))
    return img.filter(ImageFilter.GaussianBlur(radius=10))


def create_sparkle_layer(W: int, H: int) -> Image.Image:
    sw, sh = W + 300, H + 300
    img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _ in range(135):
        x, y = random.randint(0, sw), random.randint(0, sh)
        r = random.randint(2, 4)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 250, 210, 240))
    return img


def create_light_leak(W: int, H: int) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = random.randint(0, W)
    y = random.randint(0, H)
    r = random.randint(240, 420)
    draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 220, 150, 75))
    return img.filter(ImageFilter.GaussianBlur(radius=70))


def prepare_fx_layers(work_dir: Path, W: int, H: int) -> dict:
    bokeh_path = work_dir / "fx_bokeh.png"
    sparkle_path = work_dir / "fx_sparkle.png"
    leak_path = work_dir / "fx_leak.png"
    create_bokeh_layer(W, H).save(bokeh_path)
    create_sparkle_layer(W, H).save(sparkle_path)
    create_light_leak(W, H).save(leak_path)
    return {"bokeh": bokeh_path, "sparkle": sparkle_path, "leak": leak_path}


WOMEN_KEYWORDS = [
    "مريم", "آسية", "خديجة", "عائشة", "فاطمة", "حفصة", "زينب", "هاجر", "سارة",
    "المرأة", "النساء", "امرأة", "زوجة", "ابنة", "حامل", "ولادة",
]
NIQAB_SCENES = ["woman in niqab", "niqab woman praying", "niqab woman reading quran"]

THEME_POOLS = {
    "streams": [
        "crystal clear forest stream",
        "emerald river moss forest",
        "mountain spring flowing water",
        "turquoise creek stones forest",
        "glowing river nature mist",
    ],
    "waterfalls_snow": [
        "majestic waterfall misty forest",
        "waterfall emerald lagoon nature",
        "snowy mountain peaks clouds",
        "winter pine forest falling snow",
        "frozen waterfall alpine lake",
        "snow covered mountain cabin",
    ],
    "otherworldly": [
        "bioluminescent glowing forest night",
        "aurora borealis starry mountain sky",
        "mystical glowing woods sunbeams",
        "glowing fireflies dark forest",
        "surreal colorful nebula cosmos",
        "cherry blossom glowing tunnel",
        "butterflies flowers animation",
        "rose petals falling slow",
        "golden bokeh lights",
    ],
    "cottages_rain": [
        "wooden cabin flowers meadow",
        "cozy wooden cottage forest rain",
        "timber house alpine flowers",
        "raindrops blooming flowers garden",
        "wooden chalet misty mountains",
        "flower garden spring rain",
        "pink roses blooming",
    ],
    "damascus_heritage": [
        "old damascus courtyard fountain",
        "azem palace damascus courtyard",
        "umayyad mosque damascus courtyard",
        "old stone alleyway arches",
        "traditional courtyard fountain jasmine",
        "ancient middle eastern stone street",
        "islamic arch courtyard garden",
        "islamic lantern glowing",
    ],
    "cosmos_mosque": [
        "starry night milky way desert",
        "mosque dome sunset sky",
        "islamic architecture golden light",
        "golden clouds sunset",
        "moonlight serene",
    ],
}

KEYWORD_RULES = [
    (r"\b(?:عرش|كرسي|سماوات|سماء|نجوم|كواكب|كون|فضاء|مصابيح)\b", ["aurora borealis starry mountain sky", "surreal colorful nebula cosmos", "starry night milky way desert"]),
    (r"\b(?:قمر|ليل|فجر|شمس|نهار|صباح|مساء)\b", ["moonlight serene", "golden clouds sunset", "sunrise misty valley"]),
    (r"\b(?:بحر|موج|يم|طوفان|نهر|ماء|أمطار|مطر|غيث|ينبوع|عين)\b", THEME_POOLS["streams"] + ["ocean waves sunset"]),
    (r"\b(?:جبل|جبال|طور|صخر|حجر|كهف|غار|ثلج|برد)\b", THEME_POOLS["waterfalls_snow"]),
    (r"\b(?:صحراء|رمل|رمال|بادية|بيداء|وادي)\b", ["desert dunes sunset", "valley misty mountains"]),
    (r"\b(?:شجر|نخل|بستان|جنة|زهر|ورد|غابة|ربيع)\b", THEME_POOLS["otherworldly"] + THEME_POOLS["cottages_rain"]),
    (r"\b(?:نار|جهنم|بركان|ريح|عاصفة|رعد|برق)\b", ["volcano lava", "storm clouds lightning", "stormy sea waves"]),
    (r"\b(?:مسجد|كعبة|مكة|المدينة|قدس|أقصى|صلاة|توحيد|ذكر|دعاء|دار|بيت|شام|دمشق)\b", THEME_POOLS["damascus_heritage"] + THEME_POOLS["cosmos_mosque"]),
]


def _all_rich_visuals() -> list[str]:
    pool = []
    for lst in THEME_POOLS.values():
        pool.extend(lst)
    try:
        for kw in get_allowed_keywords():
            if isinstance(kw, str) and len(kw.strip()) >= 3:
                pool.append(kw.strip())
    except Exception:
        pass
    dedup = list(dict.fromkeys(pool))
    random.shuffle(dedup)
    return dedup


def suggest_visual_query(scene_text: str, seg_type: str = "", explicit_visual: str = "", used_queries: set | None = None) -> str:
    if explicit_visual and is_safe_query(explicit_visual):
        return explicit_visual

    for kw in WOMEN_KEYWORDS:
        if kw in scene_text:
            return random.choice(NIQAB_SCENES)

    matched_candidates = []
    for pattern, queries in KEYWORD_RULES:
        if re.search(pattern, scene_text):
            matched_candidates.extend(queries)

    if not matched_candidates:
        if seg_type == "adhkar":
            matched_candidates = THEME_POOLS["streams"] + THEME_POOLS["cottages_rain"] + THEME_POOLS["otherworldly"]
        elif seg_type in ("fiqh", "hadith"):
            matched_candidates = THEME_POOLS["damascus_heritage"] + THEME_POOLS["streams"] + THEME_POOLS["waterfalls_snow"] + THEME_POOLS["cottages_rain"] + THEME_POOLS["otherworldly"]
        else:
            matched_candidates = _all_rich_visuals()

    candidates = [q for q in matched_candidates if is_safe_query(q)]
    if used_queries:
        fresh = [q for q in candidates if q not in used_queries]
        if fresh:
            candidates = fresh

    if candidates:
        chosen = random.choice(candidates)
        if used_queries is not None:
            used_queries.add(chosen)
        return chosen

    safe_all = [q for q in _all_rich_visuals() if is_safe_query(q)]
    return random.choice(safe_all) if safe_all else "crystal clear forest stream"


def detect_scenes(segments: list, max_scene_dur: float | None = None) -> list:
    if max_scene_dur is None:
        total_dur = sum(float(c.get("duration", 0)) for seg in segments for c in seg.get("chunks", []))
        max_scene_dur = 22.0 if total_dur > 240.0 else 13.0

    scenes = []
    current = None
    used_queries = set()

    for idx, seg in enumerate(segments):
        stype = seg.get("type", "")
        chunks = seg.get("chunks", [])
        is_quran = (stype == "quran")

        for c_idx, chunk in enumerate(chunks):
            c_dur = float(chunk.get("duration", 0))
            if c_dur <= 0:
                continue
            c_text = chunk.get("text", "")

            need_new = (
                current is None
                or current["seg_type"] != stype
                or (not is_quran and current["duration"] >= max_scene_dur)
                or (is_quran and current["duration"] >= 26.0)
            )
            if need_new:
                query = suggest_visual_query(
                    c_text,
                    seg_type=stype,
                    explicit_visual=seg.get("visual", ""),
                    used_queries=used_queries,
                )
                current = {
                    "scene_id": len(scenes),
                    "seg_type": stype,
                    "query": query,
                    "duration": 0.0,
                    "chunk_keys": [],
                }
                scenes.append(current)

            current["duration"] += c_dur
            current["chunk_keys"].append((idx, c_idx))

    return scenes


def _find_local(query: str, used_local: set) -> str | None:
    valid_ext = (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png")
    all_local: list[Path] = []
    for base in (Path(BG_VIDEOS_DIR), Path(BG_IMAGES_DIR)):
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if f.is_file() and f.suffix.lower() in valid_ext and f.stat().st_size > 15000:
                if str(f) not in used_local:
                    all_local.append(f)

    if not all_local:
        return None

    tokens = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if len(w) >= 3]
    for f in all_local:
        name = f.stem.lower()
        if any(t in name for t in tokens):
            used_local.add(str(f))
            return str(f)

    if random.random() < 0.25:
        picked = random.choice(all_local)
        used_local.add(str(picked))
        return str(picked)
    return None


def _save_clean_jpeg(content: bytes, out_path: Path) -> bool:
    try:
        with Image.open(io.BytesIO(content)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            if min(im.size) < 400:
                return False
            im.save(out_path, "JPEG", quality=92)
        return out_path.exists() and out_path.stat().st_size > 20000
    except Exception:
        return False


def _make_islamic_fallback_bg(path: Path, W: int, H: int) -> str:
    img = Image.new("RGB", (W, H), (10, 28, 38))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        ratio = y / H
        r = int(8 + 14 * ratio)
        g = int(26 + 34 * ratio)
        b = int(38 + 22 * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    rng = random.Random(42)
    for _ in range(70):
        x, y = rng.randint(20, W - 20), rng.randint(20, H - 20)
        sz = rng.randint(1, 2)
        draw.ellipse([x - sz, y - sz, x + sz, y + sz], fill=(212, 175, 55))
    draw.rectangle([18, 18, W - 18, H - 18], outline=(180, 145, 55), width=2)
    img.save(path, "JPEG", quality=92)
    return str(path)


def _try_pixabay_video(q: str, work_dir: Path, used_urls: set, scene_id: int) -> str | None:
    if not PIXABAY_KEY:
        return None
    v_types = ["film", "all"]
    for v_type in v_types:
        for page in (random.choice([1, 1, 2]), 1):
            try:
                r = requests.get(
                    "https://pixabay.com/api/videos/",
                    params={
                        "key": PIXABAY_KEY,
                        "q": q,
                        "video_type": v_type,
                        "safesearch": "true",
                        "per_page": 50,
                        "page": page,
                        "min_width": 640,
                    },
                    timeout=20,
                )
                if r.status_code != 200:
                    continue
                hits = r.json().get("hits", [])
                if not hits:
                    continue
                random.shuffle(hits)
                for hit in hits:
                    if not is_safe_tag(hit.get("tags", "")):
                        continue
                    vids = hit.get("videos", {})
                    url = (
                        vids.get("medium", {}).get("url")
                        or vids.get("large", {}).get("url")
                        or vids.get("small", {}).get("url")
                    )
                    if not url or url in used_urls:
                        continue
                    out = work_dir / f"px_vid_{scene_id}_{hit['id']}.mp4"
                    with open(out, "wb") as f:
                        for ch in requests.get(url, timeout=60, stream=True).iter_content(16384):
                            f.write(ch)
                    if out.stat().st_size > 50000:
                        used_urls.add(url)
                        return str(out)
            except Exception:
                continue
    return None


def _try_pixabay_image(q: str, work_dir: Path, used_urls: set, scene_id: int) -> str | None:
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": PIXABAY_KEY,
                "q": q,
                "image_type": "photo",
                "safesearch": "true",
                "per_page": 40,
                "page": random.choice([1, 1, 2]),
                "min_width": 720,
            },
            timeout=20,
        )
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            random.shuffle(hits)
            for hit in hits:
                if not is_safe_tag(hit.get("tags", "")):
                    continue
                url = hit.get("largeImageURL") or hit.get("webformatURL")
                if not url or url in used_urls:
                    continue
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200:
                    out = work_dir / f"px_img_{scene_id}_{hit['id']}.jpg"
                    if _save_clean_jpeg(resp.content, out):
                        used_urls.add(url)
                        return str(out)
    except Exception:
        pass
    return None


def _try_openverse_image(q: str, work_dir: Path, used_urls: set, scene_id: int) -> str | None:
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            headers={"User-Agent": "MalakAlMarifa/1.0"},
            params={
                "q": q,
                "license_type": "commercial",
                "mature": "false",
                "filter_dead": "true",
                "extension": "jpg,png",
                "page_size": 20,
                "page": random.choice([1, 1, 2]),
            },
            timeout=20,
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        random.shuffle(results)
        for idx, item in enumerate(results):
            if item.get("mature") or item.get("watermarked"):
                continue
            url = item.get("url")
            if not url or url in used_urls:
                continue
            w = item.get("width") or 0
            h = item.get("height") or 0
            if (w and w < 600) or (h and h < 600):
                continue

            tags_raw = item.get("tags") or []
            tag_words = [t.get("name", "") if isinstance(t, dict) else str(t) for t in tags_raw]
            safety_text = f"{item.get('title', '')} {' '.join(tag_words)}".strip()
            if not safety_text or not is_safe_tag(safety_text):
                continue

            resp = requests.get(url, headers={"User-Agent": "MalakAlMarifa/1.0"}, timeout=25)
            if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", "").lower():
                out = work_dir / f"ov_img_{scene_id}_{idx}.jpg"
                if _save_clean_jpeg(resp.content, out):
                    used_urls.add(url)
                    return str(out)
    except Exception:
        pass
    return None


def fetch_media(query: str, work_dir: Path, used_urls: set, used_local: set, W: int, H: int, scene_id: int) -> str:
    share_enabled = (os.getenv("SHARE_EPISODE_MEDIA") == "1")
    share_cache = Path(tempfile.gettempdir()) / "story_media_cache"
    if share_enabled:
        share_cache.mkdir(parents=True, exist_ok=True)
        for ext in (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png"):
            cached = share_cache / f"scene_{scene_id}{ext}"
            if cached.exists() and cached.stat().st_size > 15000:
                return str(cached)

    def _maybe_cache(path_str: str) -> str:
        if share_enabled and path_str:
            src = Path(path_str)
            if src.exists() and src.suffix.lower() in (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png"):
                dst = share_cache / f"scene_{scene_id}{src.suffix.lower()}"
                if not dst.exists():
                    try:
                        shutil.copyfile(src, dst)
                    except Exception:
                        pass
        return path_str

    local = _find_local(query, used_local)
    if local:
        return _maybe_cache(local)

    fallback_queries = [query]
    for backup in random.sample(_all_rich_visuals(), min(4, len(_all_rich_visuals()))):
        if backup not in fallback_queries:
            fallback_queries.append(backup)

    is_heritage = any(k in query for k in ("damascus", "azem", "umayyad", "alley", "courtyard"))

    for q in fallback_queries:
        if not is_safe_query(q):
            continue

        if is_heritage:
            res = _try_openverse_image(q, work_dir, used_urls, scene_id)
            if res:
                return _maybe_cache(res)

        res = _try_pixabay_video(q, work_dir, used_urls, scene_id)
        if res:
            return _maybe_cache(res)

        img_fetchers = (_try_openverse_image, _try_pixabay_image) if scene_id % 2 == 1 else (_try_pixabay_image, _try_openverse_image)
        for fn in img_fetchers:
            res = fn(q, work_dir, used_urls, scene_id)
            if res:
                return _maybe_cache(res)

    fb = work_dir / f"fallback_{scene_id}.jpg"
    return _make_islamic_fallback_bg(fb, W, H)


def _load_font(size: int, is_quran: bool = False):
    fpath = QURAN_FONT_PATH if is_quran else FONT_PATH
    if HAS_RAQM:
        return ImageFont.truetype(fpath, size, layout_engine=ImageFont.Layout.RAQM)
    return ImageFont.truetype(fpath, size)


def _prepare_draw_text(line: str) -> str:
    if HAS_RAQM:
        return line
    return bidi_display(arabic_reshaper.reshape(line))


def _measure_bbox(draw, line: str, font) -> tuple[int, int, int, int]:
    txt = _prepare_draw_text(line)
    if HAS_RAQM:
        return draw.textbbox((0, 0), txt, font=font, direction="rtl")
    return draw.textbbox((0, 0), txt, font=font)


def _measure_line(draw, line: str, font) -> tuple[int, int]:
    bbox = _measure_bbox(draw, line, font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_centered(draw, y: int, line: str, font, fill, W: int):
    txt = _prepare_draw_text(line)
    if HAS_RAQM:
        bbox = draw.textbbox((0, 0), txt, font=font, direction="rtl")
        x = (W - (bbox[2] - bbox[0])) // 2 - bbox[0]
        draw.text((x, y), txt, font=font, fill=fill, direction="rtl")
    else:
        bbox = draw.textbbox((0, 0), txt, font=font)
        x = (W - (bbox[2] - bbox[0])) // 2 - bbox[0]
        draw.text((x, y), txt, font=font, fill=fill)


def _composite_with_outline(canvas: Image.Image, fg_layer: Image.Image, outline_px: int = 2, shadow_blur: int = 3):
    alpha = fg_layer.getchannel("A")
    zero = Image.new("L", canvas.size, 0)
    if outline_px > 0:
        outline_alpha = alpha.filter(ImageFilter.MaxFilter(outline_px * 2 + 1))
        if shadow_blur > 0:
            sh_alpha = outline_alpha.filter(ImageFilter.GaussianBlur(shadow_blur))
            sh_rgba = Image.merge("RGBA", (zero, zero, zero, sh_alpha))
            canvas.alpha_composite(sh_rgba, dest=(0, 3))
        outline_rgba = Image.merge("RGBA", (zero, zero, zero, outline_alpha))
        canvas.alpha_composite(outline_rgba)
    canvas.alpha_composite(fg_layer)


def _apply_cinematic_scrim(overlay: Image.Image, is_quran: bool, W: int, H: int):
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scrim)
    if is_quran:
        draw.rectangle([0, 0, W, H], fill=(4, 10, 18, 128))
    else:
        for y in range(0, 105):
            alpha = int(115 * (1.0 - y / 105.0))
            draw.line([(0, y), (W, y)], fill=(4, 8, 15, alpha))
        start_y = int(H * 0.60)
        span = H - start_y
        for y in range(start_y, H):
            ratio = (y - start_y) / max(1, span)
            alpha = int(198 * (ratio ** 1.35))
            draw.line([(0, y), (W, y)], fill=(4, 8, 15, alpha))
    overlay.alpha_composite(scrim)


def _wrap_logical(text: str, font, max_w: int, draw) -> list[str]:
    lines = []
    for para in text.splitlines():
        words = para.split()
        if not words:
            continue
        current = ""
        for w in words:
            trial = f"{current} {w}".strip() if current else w
            tw, _ = _measure_line(draw, trial, font)
            if tw <= max_w:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = w
        if current:
            lines.append(current)
    return lines


def _layout_slides(
    text: str,
    max_w: int,
    max_h: int,
    start_size: int = 48,
    min_size: int = 32,
    is_quran: bool = False,
    cinematic_subtitles: bool = False,
) -> tuple[object, list[list[str]], int]:
    dummy = Image.new("RGB", (10, 10))
    ddraw = ImageDraw.Draw(dummy)
    lh_mult = 2.35 if is_quran else (1.58 if cinematic_subtitles else 1.75)

    for size in range(start_size, min_size - 1, -2):
        font = _load_font(size, is_quran=is_quran)
        lines = _wrap_logical(text, font, max_w, ddraw)
        max_glyph_h = max((_measure_line(ddraw, ln, font)[1] for ln in lines), default=0)
        lh = max(int(size * lh_mult), max_glyph_h + 14)
        max_lines_cap = 2 if (cinematic_subtitles and not is_quran) else 99
        if len(lines) <= max_lines_cap and len(lines) * lh <= max_h:
            return font, [lines], lh

    font = _load_font(min_size, is_quran=is_quran)
    lines = _wrap_logical(text, font, max_w, ddraw)
    max_glyph_h = max((_measure_line(ddraw, ln, font)[1] for ln in lines), default=0)
    lh = max(int(min_size * lh_mult), max_glyph_h + 14)
    if cinematic_subtitles and not is_quran:
        max_lines_per_slide = 2
    else:
        max_lines_per_slide = max(2, max_h // lh)
    slides = [lines[i:i + max_lines_per_slide] for i in range(0, len(lines), max_lines_per_slide)]
    return font, slides, lh


def make_caption_slides(
    chunk_text: str,
    display_ref: str,
    reciter_name: str,
    is_quran: bool,
    ep_title: str,
    base_png_path: Path,
    W: int,
    H: int,
    c_dur: float,
    show_reciter: bool = True,
    cinematic_subtitles: bool = False,
) -> list[tuple[Path, float]]:
    if is_quran:
        clean_body = (chunk_text or "").strip()
    else:
        clean_body = re.sub(r"[*#@_\[\]]", "", chunk_text or "")
        clean_body = re.sub(r"[a-zA-Z]", "", clean_body).strip()
        clean_body = clean_body.replace("ﷺ", "صلى الله عليه وسلم")

    if not clean_body:
        return []

    if cinematic_subtitles and not is_quran:
        font, slides_lines, lh = _layout_slides(
            clean_body,
            max_w=W - 84,
            max_h=210,
            start_size=36,
            min_size=28,
            is_quran=False,
            cinematic_subtitles=True,
        )
    else:
        font, slides_lines, lh = _layout_slides(
            clean_body,
            max_w=W - 96 if is_quran else W - 90,
            max_h=H - 520 if is_quran else H - 360,
            start_size=44 if is_quran else 48,
            min_size=34 if is_quran else 32,
            is_quran=is_quran,
            cinematic_subtitles=False,
        )

    total_chars = max(1, sum(len(" ".join(sl)) for sl in slides_lines))
    outputs: list[tuple[Path, float]] = []

    title_font = _load_font(24 if cinematic_subtitles else 26, is_quran=False)
    ref_font = _load_font(28, is_quran=False)
    reciter_font = _load_font(24, is_quran=False)
    wm_font = _load_font(26 if cinematic_subtitles else 28, is_quran=False)

    clean_title = re.sub(r"[a-zA-Z]", "", ep_title or "").strip()

    for s_idx, lines in enumerate(slides_lines):
        slide_chars = max(1, len(" ".join(lines)))
        slide_dur = max(0.3, c_dur * (slide_chars / total_chars)) if len(slides_lines) > 1 else c_dur

        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if cinematic_subtitles:
            _apply_cinematic_scrim(overlay, is_quran=is_quran, W=W, H=H)

        body_fg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        meta_fg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        body_draw = ImageDraw.Draw(body_fg)
        meta_draw = ImageDraw.Draw(meta_fg)

        if clean_title:
            _draw_centered(meta_draw, 30 if cinematic_subtitles else 36, clean_title, title_font, "#D4AF37", W)

        min_top = min((_measure_bbox(body_draw, ln, font)[1] for ln in lines), default=0)
        last_bottom = _measure_bbox(body_draw, lines[-1], font)[3] if lines else lh

        if cinematic_subtitles and not is_quran:
            block_h = len(lines) * lh
            y = H - 122 - block_h + max(0, -min_top)
        else:
            ref_extra = 64 if (is_quran and display_ref) else 0
            total_h = len(lines) * lh + ref_extra
            y = max(145 + max(0, -min_top), (H - total_h) // 2)

        text_color = "#FFE066" if is_quran else "#FFFFFF"
        for line in lines:
            _draw_centered(body_draw, y, line, font, text_color, W)
            y += lh

        if is_quran and display_ref:
            ref_y = max(y + 8, (y - lh) + last_bottom + 20)
            _draw_centered(meta_draw, ref_y, display_ref, ref_font, "#D4AF37", W)

        if is_quran and reciter_name and show_reciter:
            _draw_centered(meta_draw, H - 150, f"بصوت: {reciter_name}", reciter_font, "#E8D5A0", W)

        wm_txt = _prepare_draw_text("ملاك المعرفة")
        if HAS_RAQM:
            wm_bbox = meta_draw.textbbox((0, 0), wm_txt, font=wm_font, direction="rtl")
            wx = W - (wm_bbox[2] - wm_bbox[0]) - 28 - wm_bbox[0]
            meta_draw.text((wx, H - 56), wm_txt, font=wm_font, fill="#D4AF37", direction="rtl")
        else:
            wm_bbox = meta_draw.textbbox((0, 0), wm_txt, font=wm_font)
            wx = W - (wm_bbox[2] - wm_bbox[0]) - 28 - wm_bbox[0]
            meta_draw.text((wx, H - 56), wm_txt, font=wm_font, fill="#D4AF37")

        _composite_with_outline(overlay, body_fg, outline_px=2, shadow_blur=3)
        _composite_with_outline(overlay, meta_fg, outline_px=1, shadow_blur=2)

        png_path = base_png_path.with_name(f"{base_png_path.stem}_s{s_idx}.png")
        overlay.save(png_path)
        outputs.append((png_path, slide_dur))

    if len(outputs) > 1:
        diff = c_dur - sum(d for _, d in outputs)
        last_p, last_d = outputs[-1]
        outputs[-1] = (last_p, max(0.3, last_d + diff))

    return outputs


def _render_card_slides_video(card_slides: list[tuple[Path, float]], c_dur: float, W: int, H: int, out_chunk: Path):
    """يحوّل شرائح البطاقة/الدفتر الإسلامي إلى مقطع سلس مع انتقال ذهبي ناعم بين العبارات."""
    n = len(card_slides)
    if n == 1:
        png_path, _ = card_slides[0]
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-t", f"{c_dur:.3f}", "-i", str(png_path),
                "-vf", f"scale={W}:{H},fps={VIDEO_FPS},format=yuv420p,setsar=1",
                *ENCODE_ARGS, str(out_chunk),
            ],
            check=True, capture_output=True, timeout=120,
        )
        return

    min_s = min(d for _, d in card_slides)
    fade_d = min(0.28, min_s * 0.35)
    inputs = []
    filters = []
    for i, (png_path, s_dur) in enumerate(card_slides):
        in_dur = s_dur + (fade_d if i < n - 1 else 0.0)
        inputs.extend(["-loop", "1", "-t", f"{in_dur:.3f}", "-i", str(png_path)])
        filters.append(f"[{i}:v]scale={W}:{H},fps={VIDEO_FPS},format=yuv420p,setsar=1[c{i}]")

    last_tag = "[c0]"
    offset = 0.0
    for k in range(n - 1):
        offset += float(card_slides[k][1])
        out_tag = "[vout]" if k == n - 2 else f"[cx{k}]"
        filters.append(
            f"{last_tag}[c{k+1}]xfade=transition=fade:duration={fade_d:.3f}:offset={offset:.3f}{out_tag}"
        )
        last_tag = out_tag

    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[vout]",
            "-t", f"{c_dur:.3f}",
            *ENCODE_ARGS, str(out_chunk),
        ],
        check=True, capture_output=True, timeout=180,
    )


def get_duration(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def prep_background(bg_file: str, duration: float, W: int, H: int, out_path: Path, fx_layers: dict | None = None):
    is_img = bg_file.lower().endswith((".jpg", ".jpeg", ".png"))

    if fx_layers:
        try:
            if is_img:
                total_frames = max(24, int(duration * VIDEO_FPS) + 24)
                base_cmd = ["-i", bg_file]
                base_filter = (
                    f"[0:v]scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
                    f"crop={W*2}:{H*2},"
                    f"zoompan=z='min(zoom+0.0007,1.14)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={W}x{H}:fps={VIDEO_FPS},"
                    f"colorbalance=rs=0.05:gs=0.02:bs=-0.03,"
                    f"eq=brightness=-0.09:contrast=1.08:saturation=1.18,"
                    f"vignette=PI/5,format=yuv420p[base]"
                )
            else:
                base_cmd = ["-stream_loop", "-1", "-i", bg_file]
                base_filter = (
                    f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},fps={VIDEO_FPS},"
                    f"colorbalance=rs=0.05:gs=0.02:bs=-0.03,"
                    f"eq=brightness=-0.09:contrast=1.08:saturation=1.18,"
                    f"vignette=PI/5,format=yuv420p[base]"
                )

            fx_inputs = [
                "-loop", "1", "-i", str(fx_layers["bokeh"]),
                "-loop", "1", "-i", str(fx_layers["sparkle"]),
                "-loop", "1", "-i", str(fx_layers["leak"]),
            ]
            fc = (
                f"{base_filter};"
                f"[1:v]fps={VIDEO_FPS},format=rgba,colorchannelmixer=aa=0.22[bk];"
                f"[2:v]fps={VIDEO_FPS},format=rgba,colorchannelmixer=aa=0.40[sp];"
                f"[3:v]fps={VIDEO_FPS},format=rgba,colorchannelmixer=aa=0.25[lk];"
                f"[base][bk]overlay=x='-100+50*sin(t/8)':y='-100+50*cos(t/10)':shortest=1[v1];"
                f"[v1][sp]overlay=x='-50+30*cos(t/6)':y='-50+30*sin(t/9)':shortest=1[v2];"
                f"[v2][lk]overlay=0:0:shortest=1,format=yuv420p,setsar=1[vout]"
            )
            subprocess.run(
                ["ffmpeg", "-y"] + base_cmd + fx_inputs + [
                    "-filter_complex", fc, "-map", "[vout]",
                    "-t", f"{duration:.3f}", *ENCODE_ARGS, str(out_path),
                ],
                check=True, capture_output=True, timeout=240,
            )
            return
        except Exception:
            pass

    input_flags = ["-loop", "1"] if is_img else ["-stream_loop", "-1"]
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={VIDEO_FPS},"
        f"eq=brightness=-0.10:contrast=1.06,"
        f"format=yuv420p,setsar=1"
    )
    subprocess.run(
        ["ffmpeg", "-y"] + input_flags + [
            "-i", bg_file, "-t", f"{duration:.3f}", "-vf", vf,
            *ENCODE_ARGS, str(out_path),
        ],
        check=True, capture_output=True,
    )


def prepare_scene_backgrounds(meta: dict, work_dir: Path, W: int, H: int) -> tuple[list, dict]:
    scenes = detect_scenes(meta["segments"])
    used_urls = set()
    used_local = set()
    scene_bg_map = {}
    print(f"🎨 تحضير {len(scenes)} مشهد بصري سحري متنوع...", flush=True)
    for scene in scenes:
        sid = scene["scene_id"]
        query = scene["query"]
        label = "🕌 منقبة" if query in NIQAB_SCENES else "✨"
        print(f"   {label} Scene {sid} ({scene['duration']:.1f}s): '{query}'", flush=True)
        scene_bg_map[sid] = fetch_media(query, work_dir, used_urls, used_local, W, H, sid)
    return scenes, scene_bg_map


def build_video(meta: dict, audio_path: str, output_mp4: str, W: int, H: int, work_dir: Path, show_reciter: bool = True):
    load_ayahs()
    segments = meta["segments"]
    ep_title = meta.get("title", "")
    story_id = str(meta.get("story_id", ""))

    cinema_env = os.getenv("CINEMA_ANIME_MODE", "").strip()
    if cinema_env == "1":
        use_cinema = CINEMA_AVAILABLE
    elif cinema_env == "0":
        use_cinema = False
    else:
        use_cinema = CINEMA_AVAILABLE and (not story_id.startswith("daily_"))

    card_mode = CARDS_AVAILABLE and (os.getenv("USE_ISLAMIC_CARDS", "1") == "1")

    chunk_bg_map: dict[tuple[int, int], Path] = {}
    chunk_to_scene = {}
    scene_bg_videos = {}
    scene_offsets = {}
    final_audio_input = audio_path

    if use_cinema:
        print("🎥 تفعيل مخرج الواقعية السينمائية 3D + الدفاتر الإسلامية المطرّزة", flush=True)
        cinema_meta = meta
        if card_mode:
            cinema_meta = {
                **meta,
                "segments": [
                    ({**s, "chunks": []} if s.get("type") in CARD_SEG_TYPES else s)
                    for s in segments
                ],
            }
        cinema_plan = plan_episode_shots(cinema_meta)
        chunk_bg_map = prepare_and_render_episode_chunks(cinema_meta, cinema_plan, W, H, work_dir)
        final_audio_input = build_halal_ambient_mix(meta, cinema_plan, audio_path, work_dir)
    else:
        fx_layers = prepare_fx_layers(work_dir, W, H)
        scenes, scene_bg_map = prepare_scene_backgrounds(meta, work_dir, W, H)
        for scene in scenes:
            sid = scene["scene_id"]
            for key in scene["chunk_keys"]:
                chunk_to_scene[key] = sid
            scene_dur = scene["duration"]
            if scene_dur > 0:
                bg_mp4 = work_dir / f"bg_scene_{sid}_{W}x{H}.mp4"
                prep_background(scene_bg_map[sid], scene_dur + 0.5, W, H, bg_mp4, fx_layers=fx_layers)
                scene_bg_videos[sid] = bg_mp4
        scene_offsets = {sc["scene_id"]: 0.0 for sc in scenes}

    concat_lines = []

    for seg_idx, seg in enumerate(segments):
        stype = seg.get("type", "")
        chunks = seg.get("chunks", [])
        is_quran = (stype == "quran")
        is_card_seg = card_mode and (stype in CARD_SEG_TYPES)
        ayahs = seg.get("ayah_numbers", [])

        for c_idx, chunk_data in enumerate(chunks):
            c_dur = float(chunk_data.get("duration", 0))
            if c_dur <= 0:
                continue

            c_text = chunk_data.get("text", "")
            out_chunk = work_dir / f"chunk_{seg_idx}_{c_idx}.mp4"

            # نمط الدفتر/البطاقات المطرّزة للأحاديث والنصوص والـ CTA
            if is_card_seg and c_text.strip():
                if not use_cinema and (seg_idx, c_idx) in chunk_to_scene:
                    scene_offsets[chunk_to_scene[(seg_idx, c_idx)]] += c_dur

                card_slides = render_islamic_card_slides(
                    chunk_text=c_text,
                    seg_type=stype,
                    ep_title=ep_title,
                    font_path=FONT_PATH,
                    base_png_path=work_dir / f"card_{seg_idx}_{c_idx}.png",
                    W=W,
                    H=H,
                    c_dur=c_dur,
                )
                if card_slides:
                    _render_card_slides_video(card_slides, c_dur, W, H, out_chunk)
                    concat_lines.append(f"file '{out_chunk.resolve()}'")
                    continue

            if use_cinema:
                bg_video = chunk_bg_map[(seg_idx, c_idx)]
                bg_cmd = ["-i", str(bg_video)]
            else:
                sid = chunk_to_scene[(seg_idx, c_idx)]
                bg_video = scene_bg_videos[sid]
                bg_offset = scene_offsets[sid]
                scene_offsets[sid] += c_dur
                bg_cmd = ["-ss", f"{bg_offset:.3f}", "-t", f"{c_dur:.3f}", "-i", str(bg_video)]

            reciter = chunk_data.get("reciter") or seg.get("reciter", "")
            display_ref = ""

            if is_quran:
                surah = int(chunk_data.get("surah") or seg.get("surah") or 0)
                a_num = int(chunk_data.get("ayah") or (ayahs[c_idx] if c_idx < len(ayahs) else 0))
                sname = (
                    chunk_data.get("surah_name")
                    or seg.get("surah_name")
                    or SURAH_NAME_LOOKUP.get(surah, "")
                ).replace("سورة ", "").strip()

                real_text = get_ayah_text_safe(surah, a_num) if (surah and a_num) else ""
                if real_text:
                    c_text = f"﴿ {real_text} ﴾"
                    display_ref = f"[ سورة {sname} : {a_num} ]" if sname else f"[ {surah}:{a_num} ]"

            slides = make_caption_slides(
                chunk_text=c_text,
                display_ref=display_ref,
                reciter_name=reciter,
                is_quran=is_quran,
                ep_title=ep_title,
                base_png_path=work_dir / f"ov_{seg_idx}_{c_idx}.png",
                W=W,
                H=H,
                c_dur=c_dur,
                show_reciter=show_reciter,
                cinematic_subtitles=use_cinema,
            )

            if not slides:
                subprocess.run(
                    ["ffmpeg", "-y"] + bg_cmd + ["-t", f"{c_dur:.3f}", *ENCODE_ARGS, str(out_chunk)],
                    check=True, capture_output=True,
                )
            else:
                slide_inputs = []
                slide_filters = []
                for s_i, (png_path, s_dur) in enumerate(slides):
                    in_idx = s_i + 1
                    slide_inputs.extend(["-loop", "1", "-t", f"{s_dur:.3f}", "-i", str(png_path)])
                    slide_filters.append(f"[{in_idx}:v]fps={VIDEO_FPS},format=rgba[t{s_i}]")

                concat_txt = "".join(f"[t{k}]" for k in range(len(slides)))
                fc = ";".join(slide_filters) + f";{concat_txt}concat=n={len(slides)}:v=1:a=0[txt];[0:v][txt]overlay=shortest=1,format=yuv420p,setsar=1[v]"
                subprocess.run(
                    ["ffmpeg", "-y"] + bg_cmd + slide_inputs + [
                        "-filter_complex", fc, "-map", "[v]",
                        "-t", f"{c_dur:.3f}",
                        *ENCODE_ARGS, str(out_chunk),
                    ],
                    check=True, capture_output=True,
                )

            concat_lines.append(f"file '{out_chunk.resolve()}'")

    audio_dur = get_duration(audio_path)
    concat_file = work_dir / f"concat_{W}x{H}.txt"
    concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

    Path(output_mp4).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-i", final_audio_input,
            "-map", "0:v", "-map", "1:a",
            *FINAL_ENCODE_ARGS,
            "-c:a", "aac", "-ac", "1", "-b:a", "64k",
            "-movflags", "+faststart",
            "-shortest",
            output_mp4,
        ],
        check=True, capture_output=True,
    )
    size_mb = Path(output_mp4).stat().st_size / 1024 / 1024
    print(f"✅ {Path(output_mp4).name}: {size_mb:.1f} MB | {audio_dur/60:.1f} دقيقة", flush=True)


def main():
    if len(sys.argv) < 5:
        print("الاستخدام: python story_video.py <meta.json> <audio.mp3> <output.mp4> <yt|social>")
        sys.exit(1)

    meta_path, audio_path, output_path, orientation = sys.argv[1:5]
    if orientation not in DIMENSIONS:
        print(f"❌ وضع غير معروف: {orientation}")
        sys.exit(1)

    dims = DIMENSIONS[orientation]
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix=f"ep_{orientation}_"))

    try:
        build_video(
            meta=meta,
            audio_path=audio_path,
            output_mp4=output_path,
            W=dims["w"],
            H=dims["h"],
            work_dir=work_dir,
            show_reciter=(orientation == "social"),
        )
    finally:
        cleanup_dir(work_dir)


if __name__ == "__main__":
    main()
