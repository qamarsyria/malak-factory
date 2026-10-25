"""توليد الصوت: Edge TTS للسرد (مجاني) + Gemini TTS للقرآن (مع circuit breaker)."""
import os
import sys
import json
import asyncio
import subprocess
import tempfile
import traceback
import shutil
from pathlib import Path

import requests
import edge_tts

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API  # noqa: E402
from voice import generate as gemini_generate  # noqa: E402
from sfx_manager import fetch_sfx  # noqa: E402
from utilities import cleanup_dir, notify_telegram, safe_copy  # noqa: E402


# ═════════════════════════════════════════════════════════════
# الإعدادات
# ═════════════════════════════════════════════════════════════
TARGET_BR = "192k"
SFX_VOLUME = 0.05

# Edge TTS: مجاني، بلا حدود، للسرد الطويل
# جعلناه أبطأ بنسبة 10% ليكون أكثر خشوعاً وتمهلاً
EDGE_VOICE = "ar-SA-HamedNeural"
EDGE_RATE = "-10%" 
EDGE_VOLUME = "+0%"

# Gemini TTS: للآيات القرآنية فقط
VOICE_QURAN = "Fenrir"
QURAN_STYLE = (
    "(recite in a slow, solemn, tajweed-compliant tone "
    "with pauses at proper waqf marks and full tashkeel)"
)

MAX_GEMINI_TTS_PER_EPISODE = 8
MAX_GEMINI_RETRIES = 2


# ═════════════════════════════════════════════════════════════
# أدوات مساعدة
# ═════════════════════════════════════════════════════════════
def normalize(input_path, output_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path), "-ar", "44100", "-ac", "1",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
         "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(output_path)],
        check=True, capture_output=True,
    )


def fetch_ayah_text(surah, ayah):
    try:
        r = requests.get(f"{QURAN_API}/ayah/{surah}:{ayah}", timeout=10)
        if r.status_code == 200:
            return r.json()["data"]["text"]
    except Exception as e:
        print(f"   ⚠️ فشل جلب آية {surah}:{ayah}: {e}", flush=True)
    return ""


def get_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def add_fade(input_path, output_path):
    dur = get_duration(input_path)
    if dur <= 0:
        safe_copy(input_path, output_path)
        return
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path), "-af",
         f"afade=t=in:st=0:d=0.2,afade=t=out:st={max(0, dur - 0.25)}:d=0.25",
         "-c:a", "libmp3lame", "-b:a", TARGET_BR, str(output_path)],
        check=True, capture_output=True,
    )


# ═════════════════════════════════════════════════════════════
# Edge TTS
# ═════════════════════════════════════════════════════════════
async def _edge_tts_async(text, output_path):
    communicate = edge_tts.Communicate(
        text, EDGE_VOICE, rate=EDGE_RATE, volume=EDGE_VOLUME,
    )
    await communicate.save(str(output_path))


def edge_tts_generate(text, output_path):
    asyncio.run(_edge_tts_async(text, output_path))
    return output_path


# ═════════════════════════════════════════════════════════════
# Gemini TTS (للآيات فقط)
# ═════════════════════════════════════════════════════════════
def try_gemini_quran(text, output_path):
    for attempt in range(1, MAX_GEMINI_RETRIES + 1):
        try:
            gemini_generate(
                text, str(output_path),
                voice=VOICE_QURAN, item_type="quran",
                emotion_hint=QURAN_STYLE,
            )
            return True
        except RuntimeError as e:
            if "مستنفدة" in str(e):
                print(f"   ⚠️ مفاتيح Gemini مستنفدة", flush=True)
                return False
            print(f"   ⚠️ محاولة Gemini {attempt} فشلت: {e}", flush=True)
        except Exception as e:
            print(f"   ⚠️ محاولة Gemini {attempt} فشلت: {e}", flush=True)
    return False


# ═════════════════════════════════════════════════════════════
# بناء الحلقة الصوتية
# ═════════════════════════════════════════════════════════════
def build_episode_audio(episode_json_path, output_mp3):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    parts, meta_segments, idx = [], [], 0
    all_segs = ep.get("segments", [])
    total = len(all_segs)
    gemini_quran_count = 0
    gemini_available = True

    try:
        print(f"🎙️ توليد الصوت: {total} مقطع", flush=True)

        for pos, seg in enumerate(all_segs):
            seg_type = seg.get("type")
            raw_path = work_dir / f"raw_{idx:03d}.mp3"
            fade_path = work_dir / f"fade_{idx:03d}.mp3"
            norm_path = work_dir / f"norm_{idx:03d}.mp3"
            is_first = (pos == 0)
            is_last = (pos == total - 1)

            # 1. السرد / CTA / Teaser / Intro → Edge TTS
            if seg_type in ("narration", "teaser", "cta", "outro", "intro"):
                style = seg.get("emotion_hint") or "(in a warm, narrative tone)"
                visual = seg.get("visual", "mosque")

                print(f"🎙️ Edge TTS [{seg_type}] ({len(seg['text'])} حرف)...", flush=True)
                edge_tts_generate(seg["text"], str(raw_path))

                src = str(raw_path)
                sfx = fetch_sfx(seg.get("sfx", "")) if seg.get("sfx") else ""
                if sfx and os.path.exists(sfx):
                    mixed = work_dir / f"mixed_{idx:03d}.mp3"
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", str(raw_path),
                         "-stream_loop", "-1", "-i", sfx,
                         "-filter_complex",
                         f"[1:a]volume={SFX_VOLUME}[sfx];"
                         f"[0:a][sfx]amix=inputs=2:duration=first[out]",
                         "-map", "[out]", "-c:a", "libmp3lame",
                         "-b:a", "192k", str(mixed)],
                        check=True, capture_output=True,
                    )
                    src = str(mixed)

                if is_first or is_last:
                    add_fade(src, str(fade_path))
                else:
                    safe_copy(src, str(fade_path))

                normalize(str(fade_path), str(norm_path))
                meta = {
                    "index": idx, "type": seg_type, "text": seg["text"],
                    "visual": visual, "duration": get_duration(str(norm_path)),
                }
                if seg_type == "teaser" and seg.get("next_episode_title"):
                    meta["teaser_data"] = {
                        "next_episode_title": seg.get("next_episode_title"),
                        "url": seg.get("url"),
                        "platform_name": "ملاك المعرفة",
                    }
                meta_segments.append(meta)
                parts.append(str(norm_path))

            # 2. الآيات → Gemini TTS (مع circuit breaker)
            elif seg_type == "quran":
                surah = seg["surah"]
                ayahs = seg["ayah_numbers"]
                sname = seg.get("surah_name") or f"سورة {surah}"

                if len(ayahs) == 1:
                    txt = fetch_ayah_text(surah, ayahs[0])
                    disp = f"{sname}: {ayahs[0]}"
                else:
                    txt = " ".join(filter(None, (fetch_ayah_text(surah, a) for a in ayahs)))
                    disp = f"{sname}: {ayahs[0]}-{ayahs[-1]}"

                if not txt:
                    print(f"   ⚠️ تخطي آية {disp} (نص فارغ)", flush=True)
                    idx += 1
                    continue

                generated = False

                if gemini_available and gemini_quran_count < MAX_GEMINI_TTS_PER_EPISODE:
                    print(f"🎙️ Gemini TTS [آية {disp}]...", flush=True)
                    generated = try_gemini_quran(txt, str(raw_path))
                    if generated:
                        gemini_quran_count += 1
                    else:
                        gemini_available = False
                        print(f"   🔒 تعطيل Gemini TTS لبقية الحلقة", flush=True)

                if not generated:
                    print(f"🎙️ Edge TTS [آية {disp}]...", flush=True)
                    edge_tts_generate(txt, str(raw_path))

                if is_first or is_last:
                    add_fade(str(raw_path), str(fade_path))
                else:
                    safe_copy(str(raw_path), str(fade_path))

                normalize(str(fade_path), str(norm_path))
                meta_segments.append({
                    "index": idx, "type": "quran", "text": txt,
                    "display": disp, "visual": "quran book",
                    "duration": get_duration(str(norm_path)),
                    "voice": "gemini" if generated else "edge",
                })
                parts.append(str(norm_path))

            idx += 1

        if not parts:
            raise RuntimeError("لا توجد مقاطع صوتية")

        print(f"📊 إجمالي: {gemini_quran_count} آية بـ Gemini، "
              f"{len(parts) - gemini_quran_count} مقطع بـ Edge TTS", flush=True)

        concat_file = work_dir / "final.txt"
        concat_file.write_text("\n".join([f"file '{p}'" for p in parts]), encoding="utf-8")
        merged = work_dir / "merged_raw.mp3"
        print("🔗 دمج المقاطع...", flush=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_file), "-codec:a", "libmp3lame",
             "-b:a", TARGET_BR, str(merged)],
            check=True, capture_output=True,
        )

        # 🛑 تم إزالة فلتر التسريع (asetrate/atempo) نهائياً!
        # الصوت الآن سيتطابق 100% مع مدد الفيديو المحسوبة.
        print("✅ الصوت جاهز ومتزامن تماماً...", flush=True)
        Path(output_mp3).parent.mkdir(parents=True, exist_ok=True)
        
        # ننسخ الملف النهائي مباشرة إلى المخرج
        shutil.copy(str(merged), str(output_mp3))

        meta_path = str(output_mp3).replace(".mp3", "_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "episode": ep.get("episode_number", 1),
                "title": ep.get("title", ""),
                "story_id": ep.get("story_id", ""),
                "total_duration": get_duration(output_mp3),
                "gemini_quran_count": gemini_quran_count,
                "segments": meta_segments,
            }, f, ensure_ascii=False, indent=2)

        print(f"✅ صوت: {output_mp3} | {get_duration(output_mp3)/60:.1f} د", flush=True)

    finally:
        cleanup_dir(work_dir)


if __name__ == "__main__":
    try:
        if len(sys.argv) < 3:
            print("الاستخدام: story_audio.py <episode.json> <output.mp3>")
            sys.exit(1)
        build_episode_audio(sys.argv[1], sys.argv[2])
    except Exception as e:
        traceback.print_exc()
        notify_telegram(f"فشل story_audio: {e}", "error")
        sys.exit(1)
