#!/usr/bin/env python3
"""
scripts/whispers_audio.py
=========================
يبني الصوت البشري الجاف (بدون موسيقى وبدون تنهيد هوائي يربك RVC) لكل مقطع،
ويجهّز مسار الموسيقى الخلفية وملف الميتاداتا الدقيق `out/<ep>_meta.json`.
"""

import json
import os
import random
import subprocess
import sys
from pathlib import Path
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import gemini_tts_with_pool

# أصوات القاعدة المناسبة قبل RVC (واضحة النبرة وعاطفية بدون تشويش)
BASE_VOICE_HE = os.environ.get("BASE_VOICE_HE", "Charon").strip()
BASE_VOICE_SHE = os.environ.get("BASE_VOICE_SHE", "Aoede").strip()

FREESOUND_API_KEY = os.environ.get("FREESOUND_API_KEY", "").strip()


def _get_dur(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def _pick_local_music(music_mood: str) -> Path | None:
    music_root = REPO_ROOT / "assets" / "music"
    if not music_root.exists():
        return None
    sub = music_root / ("dua" if music_mood == "spiritual_warm" else "romantic")
    pool = []
    if sub.exists():
        pool.extend([p for p in sub.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")])
    if not pool:
        pool.extend([p for p in music_root.rglob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")])
    return random.choice(pool) if pool else None


def _download_freesound(queries: list[str], out_path: Path) -> bool:
    if not FREESOUND_API_KEY:
        return False
    for q in queries:
        try:
            r = requests.get(
                "https://freesound.org/apiv2/search/text/",
                params={
                    "query": q,
                    "token": FREESOUND_API_KEY,
                    "fields": "id,name,duration,previews",
                    "filter": "duration:[25 TO 240]",
                    "page_size": 10,
                },
                timeout=20,
            )
            if r.status_code != 200:
                continue
            results = r.json().get("results", [])
            if not results:
                continue
            random.shuffle(results[:6])
            for snd in results[:6]:
                url = (snd.get("previews") or {}).get("preview-hq-mp3") or (snd.get("previews") or {}).get("preview-lq-mp3")
                if url:
                    data = requests.get(url, timeout=45).content
                    if len(data) > 10_000:
                        out_path.write_bytes(data)
                        print(f"🎵 [Freesound] {snd.get('name', '')[:45]}", flush=True)
                        return True
        except Exception:
            continue
    return False


def _generate_procedural_pad(out_wav: Path, duration: float = 90.0, mood: str = "romantic_strings") -> Path:
    if mood == "spiritual_warm":
        expr = "0.08*sin(2*PI*146.83*t)+0.06*sin(2*PI*220.0*t)+0.04*sin(2*PI*293.66*t)"
    else:
        expr = "0.08*sin(2*PI*174.61*t)+0.06*sin(2*PI*220.0*t)+0.05*sin(2*PI*261.63*t)+0.03*sin(2*PI*329.63*t)"

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={duration:.1f}",
            "-f", "lavfi", "-i", f"anoisesrc=c=pink:r=44100:a=0.012:d={duration:.1f}",
            "-filter_complex",
            "[0:a]lowpass=f=650,tremolo=f=0.25:d=0.35[pad];"
            "[1:a]bandpass=f=850:width_type=h:w=900[rain];"
            "[pad][rain]amix=inputs=2:normalize=0,afade=t=in:st=0:d=2.5",
            "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav),
        ],
        check=True, capture_output=True, timeout=60,
    )
    return out_wav


def prepare_bg_music(ep: dict, work_dir: Path) -> Path:
    music_mood = ep.get("music_mood", "romantic_strings")
    bg_wav = work_dir / "bg_music.wav"

    local = _pick_local_music(music_mood)
    if local:
        print(f"🎵 [Local Music] استخدام: {local.name}", flush=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(local), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(bg_wav)],
            check=True, capture_output=True, timeout=60,
        )
        return bg_wav

    dl_mp3 = work_dir / "bg_dl.mp3"
    queries = (
        ["spiritual ambient pad", "soft oud peaceful", "islamic calm instrumental"]
        if music_mood == "spiritual_warm"
        else ["romantic emotional piano", "cinematic soft strings love", "ambient dreamy piano"]
    )
    if _download_freesound(queries, dl_mp3):
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(dl_mp3), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(bg_wav)],
            check=True, capture_output=True, timeout=60,
        )
        return bg_wav

    print("🎼 [Ambient Pad] توليد خلفية هادئة ناعمة عبر FFmpeg...", flush=True)
    return _generate_procedural_pad(bg_wav, duration=90.0, mood=music_mood)


def build_whisper_audio(json_path: str, out_mp3: str) -> bool:
    ep_path = Path(json_path)
    ep = json.loads(ep_path.read_text(encoding="utf-8"))
    out_mp3_p = Path(out_mp3)
    out_mp3_p.parent.mkdir(parents=True, exist_ok=True)

    ep_stem = out_mp3_p.stem
    work_dir = out_mp3_p.parent / "audio_work" / ep_stem
    work_dir.mkdir(parents=True, exist_ok=True)

    segments = ep.get("segments") or []
    if not segments:
        raise RuntimeError("❌ لا توجد مقاطع نصية في ملف الحلقة.")

    meta_segments = []
    dry_concat_list = []

    for i, seg in enumerate(segments):
        tts_text = (seg.get("tts_text") or seg.get("text") or "").strip()
        display_text = (seg.get("display_text") or seg.get("text") or tts_text).strip()
        if not tts_text:
            continue

        speaker = str(seg.get("speaker", "he")).strip().lower()
        if speaker not in ("he", "she"):
            speaker = "he"

        voice = BASE_VOICE_SHE if speaker == "she" else BASE_VOICE_HE
        gender_hint = "female" if (speaker == "she" and voice != "Charon") else "male"

        # إزالة التنهيد الهوائي قبل RVC مع إبقاء الوقفات ليبقى التردد نقياً وطبيعياً
        rvc_safe_tts = tts_text.replace("<sigh>", "<short pause>")
        style = (
            "clear, warm, gentle, calm natural speaking voice, smooth melodic flow, clean articulation"
            if speaker == "she"
            else seg.get("style", "warm, tender, deeply affectionate, calm natural speaking voice")
        )
        pause_ms = int(seg.get("pause_after_ms") or 450)

        seg_wav = work_dir / f"dry_seg_{i:03d}_{speaker}.wav"
        print(f"🎙️ [{i+1}/{len(segments)}] توليد صوت ({speaker} | {voice}): {display_text[:55]}...", flush=True)
        gemini_tts_with_pool(rvc_safe_tts, str(seg_wav), voice=voice, style=style, gender_hint=gender_hint)

        dur = _get_dur(seg_wav)
        dry_concat_list.append((seg_wav, pause_ms))
        meta_segments.append({
            "index": i,
            "speaker": speaker,
            "base_voice": voice,
            "display_text": display_text,
            "tts_text": tts_text,
            "featured_names": seg.get("featured_names", []),
            "is_climax": bool(seg.get("is_climax", False)),
            "pause_after_ms": pause_ms,
            "dry_wav": str(seg_wav.resolve()),
            "dry_duration": dur,
        })

    if not meta_segments:
        raise RuntimeError("❌ لم يتم توليد أي مقاطع صوتية.")

    concat_inputs = []
    for idx, (wav_p, pause_ms) in enumerate(dry_concat_list):
        concat_inputs.append(wav_p)
        if idx < len(dry_concat_list) - 1 and pause_ms > 0:
            sil_p = work_dir / f"sil_{idx:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                 "-t", f"{pause_ms / 1000.0:.3f}", "-c:a", "pcm_s16le", str(sil_p)],
                check=True, capture_output=True,
            )
            concat_inputs.append(sil_p)

    concat_txt = work_dir / "dry_concat.txt"
    concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in concat_inputs), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
         "-codec:a", "libmp3lame", "-b:a", "192k", str(out_mp3_p)],
        check=True, capture_output=True, timeout=120,
    )

    bg_wav = prepare_bg_music(ep, work_dir)

    meta = {
        "episode_id": ep.get("episode_id", ep_stem),
        "title": ep.get("title", ""),
        "theme_id": ep.get("theme_id", "romantic"),
        "scene_category": ep.get("scene_category", "fantasy_fairies"),
        "music_mood": ep.get("music_mood", "romantic_strings"),
        "ambient_mood": ep.get("ambient_mood", "starlight_magic"),
        "bg_music_wav": str(bg_wav.resolve()),
        "work_dir": str(work_dir.resolve()),
        "segments": meta_segments,
    }
    meta_path = out_mp3_p.with_name(f"{ep_stem}_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ تم توليد الصوت الجاف والميتاداتا: {out_mp3_p} + {meta_path.name}", flush=True)
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/whispers_audio.py <episode.json> <out.mp3>")
        sys.exit(1)
    build_whisper_audio(sys.argv[1], sys.argv[2])
