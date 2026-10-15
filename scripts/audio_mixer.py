"""دمج صوت الراوي (edge-tts + atempo) + الآيات (everyayah)."""
import asyncio
import re
import subprocess
import tempfile
import time
from pathlib import Path
import requests

# ============================================================
# إصلاح edge-tts: توجيه للخادم الجديد
# ============================================================
try:
    import edge_tts
    edge_tts.communicate.WSS_URL = (
        "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
    )
    print("✅ edge-tts: تم توجيه للخادم الجديد")
except Exception as e:
    print(f"⚠️  edge-tts: {e}")

from config import (
    QURAN_RECITER, QURAN_BASE,
    TTS_VOICE, TTS_RATE,
)


DEFAULT_VOICE = TTS_VOICE or "ar-EG-ShakirNeural"
DEFAULT_RATE = TTS_RATE or "-10%"

# سرعات atempo للتجربة (كل نسخة بسرعة مختلفة)
ATEMPO_VARIANTS = [
    ("slow",    0.55),   # تباطؤ 45%
    ("slower",  0.45),   # تباطؤ 55%
    ("slowest", 0.35),   # تباطؤ 65%
]


# ============================================================
# دوال مساعدة
# ============================================================
def _clean_for_tts(text):
    text = re.sub(r"[\u064B-\u0652\u0670\u0640]", "", text)
    text = re.sub(r"[ۖۗۘۙۚۛ]", "", text)
    text = text.replace("۝", " ")
    return text.strip()


def _get_duration(file_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", file_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0


# ============================================================
# 1) edge-tts + atempo
# ============================================================
async def _edge_async(text, out_path, voice, rate):
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(out_path)


def _edge_tts(text, out_mp3, voice=DEFAULT_VOICE,
              rate=DEFAULT_RATE, atempo=0.5):
    """edge-tts + atempo للتبطيء الإضافي."""
    clean = _clean_for_tts(text)
    if not clean:
        raise RuntimeError("النص فارغ بعد التنظيف")

    raw_mp3 = out_mp3.replace(".mp3", "_raw.mp3")

    # 1) edge-tts
    asyncio.run(_edge_async(clean, raw_mp3, voice, rate))

    if not Path(raw_mp3).exists() or Path(raw_mp3).stat().st_size < 2048:
        raise RuntimeError("edge-tts: الملف الناتج صغير جداً")

    # 2) atempo
    if atempo >= 0.5:
        filter_chain = f"atempo={atempo}"
    else:
        filter_chain = f"atempo=0.5,atempo={atempo / 0.5}"

    subprocess.run(
        ["ffmpeg", "-y", "-i", raw_mp3,
         "-filter:a", filter_chain,
         "-codec:a", "libmp3lame", "-q:a", "4",
         out_mp3],
        check=True, capture_output=True, timeout=120,
    )

    if not Path(out_mp3).exists() or Path(out_mp3).stat().st_size < 2048:
        raise RuntimeError("الملف النهائي صغير جداً")


# ============================================================
# 2) gTTS احتياطي
# ============================================================
def _gtts_fallback(text, out_mp3, speed=0.5):
    from gtts import gTTS
    clean = _clean_for_tts(text)
    raw = out_mp3.replace(".mp3", "_raw.mp3")
    gTTS(text=clean, lang="ar", slow=False).save(raw)

    if speed >= 0.5:
        filter_chain = f"atempo={speed}"
    else:
        filter_chain = f"atempo=0.5,atempo={speed / 0.5}"

    subprocess.run(
        ["ffmpeg", "-y", "-i", raw,
         "-filter:a", filter_chain,
         "-codec:a", "libmp3lame", "-q:a", "4",
         out_mp3],
        check=True, capture_output=True, timeout=120,
    )


# ============================================================
# 3) everyayah للآيات
# ============================================================
def _fetch_quran_ayah(surah, ayah, out_path):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE}/{QURAN_RECITER}/{filename}"

    print(f"   📖 {surah}:{ayah} → {filename}")

    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} — {url}")

    Path(out_path).write_bytes(r.content)
    if Path(out_path).stat().st_size < 2048:
        raise RuntimeError("ملف صغير جداً")


# ============================================================
# 4) بناء المقاطع
# ============================================================
def generate_quran_audio(segment, out_path):
    ayah_numbers = segment.get("ayah_numbers") or []
    surah = segment.get("_surah") or 12

    if not ayah_numbers:
        raise RuntimeError("لا يوجد أرقام آيات")

    if len(ayah_numbers) == 1:
        _fetch_quran_ayah(surah, ayah_numbers[0], out_path)
        duration = _get_duration(out_path)
        print(f"   ✅ {duration:.1f} ثانية")
        return duration

    temp_dir = Path(tempfile.mkdtemp())
    files = []
    for n, ayah in enumerate(ayah_numbers, 1):
        f = temp_dir / f"ayah-{n:02d}.mp3"
        _fetch_quran_ayah(surah, ayah, str(f))
        files.append(str(f))
        time.sleep(0.3)

    concat_file = temp_dir / "concat.txt"
    with open(concat_file, "w", encoding="utf-8") as cf:
        for f in files:
            cf.write(f"file '{f}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-c", "copy", out_path],
        check=True, capture_output=True, timeout=120,
    )

    duration = _get_duration(out_path)
    print(f"   ✅ {len(ayah_numbers)} آيات = {duration:.1f} ثانية")
    return duration


def generate_narration_audio(segment, out_path, atempo=0.5):
    text = segment.get("text", "").strip()
    if not text:
        return None

    print(f"   🔊 [narration] {text[:60]}...")

    # 1) edge-tts
    for attempt in range(1, 3):
        try:
            _edge_tts(text, out_path, atempo=atempo)
            duration = _get_duration(out_path)
            print(f"   ✅ edge-tts: {duration:.1f} ثانية")
            return duration
        except Exception as e:
            print(f"   ⚠️  edge-tts محاولة {attempt}: {str(e)[:100]}")
            if attempt < 2:
                time.sleep(3)

    # 2) gTTS احتياطي
    try:
        print(f"   🔄 gTTS احتياطي...")
        _gtts_fallback(text, out_path, speed=atempo)
        duration = _get_duration(out_path)
        print(f"   ✅ gTTS: {duration:.1f} ثانية")
        return duration
    except Exception as e:
        print(f"   ❌ gTTS فشل أيضاً: {str(e)[:100]}")
        return None


# ============================================================
# 5) بناء 3 نسخ بسرعات مختلفة
# ============================================================
def build_variant(episode, variant_name, atempo):
    """يبني نسخة واحدة بسرعة محددة."""
    print(f"\n{'='*60}")
    print(f"🎬 النسخة: {variant_name} (atempo={atempo})")
    print(f"{'='*60}")

    out_path = f"out/episode-audio-{variant_name}.mp3"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    segments = episode.get("segments", [])
    surah = episode.get("surah", 12)

    temp_dir = Path(tempfile.mkdtemp())
    segment_files = []
    failed = 0

    for i, seg in enumerate(segments, 1):
        seg_type = seg["type"]
        seg_file = temp_dir / f"seg-{i:02d}.mp3"
        seg["_surah"] = surah

        try:
            if seg_type == "quran":
                duration = generate_quran_audio(seg, str(seg_file))
            elif seg_type in ("narration", "intro", "outro"):
                duration = generate_narration_audio(
                    seg, str(seg_file), atempo=atempo
                )
            else:
                duration = None

            if duration:
                segment_files.append(str(seg_file))
            else:
                failed += 1

        except Exception as e:
            print(f"   ❌ فشل المقطع {i}: {str(e)[:150]}")
            failed += 1

    if not segment_files:
        print(f"   ⚠️  لا مقاطع للنسخة {variant_name}")
        return None

    concat_file = temp_dir / "concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-c", "copy", out_path],
        check=True, capture_output=True, timeout=180,
    )

    duration = _get_duration(out_path)
    print(f"\n   ✅ {out_path}")
    print(f"   المدة: {duration/60:.1f} دقيقة ({duration:.0f} ث)")
    print(f"   نجح: {len(segment_files)} | فشل: {failed}")
    return out_path


def build_full_audio(episode):
    """يبني 3 نسخ بسرعات مختلفة للتجربة."""
    print(f"\n🎙️  بناء 3 نسخ بسرعات مختلفة")
    print(f"   السورة: {episode.get('surah', 12)} | القارئ: {QURAN_RECITER}")
    print(f"   الصوت: {DEFAULT_VOICE} | rate: {DEFAULT_RATE}")
    print("=" * 60)

    for name, atempo in ATEMPO_VARIANTS:
        try:
            build_variant(episode, name, atempo)
        except Exception as e:
            print(f"❌ فشل نسخة {name}: {e}")

    print(f"\n{'='*60}")
    print("✅ 3 نسخ جاهزة:")
    for name, atempo in ATEMPO_VARIANTS:
        print(f"   - out/episode-audio-{name}.mp3 (atempo={atempo})")
    print(f"{'='*60}")


if __name__ == "__main__":
    import json
    ep_file = "data/stories/yusuf/episode-01.json"
    episode = json.loads(Path(ep_file).read_text(encoding="utf-8"))
    build_full_audio(episode)
