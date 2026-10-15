"""دمج صوت الراوي + الآيات — بدون موسيقى (المرحلة الأولى)."""
import base64
import subprocess
import tempfile
import wave
from pathlib import Path
import requests
from config import GEMINI_API_KEY


def _is_ok(p):
    return Path(p).exists() and Path(p).stat().st_size > 2048


def _gemini_tts(text, out_mp3, voice="Charon"):
    """Gemini TTS."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY غير موجود")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}
                }
            }
        }
    }
    r = requests.post(url, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError("لا يوجد candidates")
    parts = (cands[0].get("content") or {}).get("parts") or []
    if not parts:
        raise RuntimeError("لا يوجد parts")
    inline = parts[0].get("inlineData") or {}
    audio_b64 = inline.get("data")
    if not audio_b64:
        raise RuntimeError("لا يوجد audio data")
    raw = base64.b64decode(audio_b64)
    wav = out_mp3.replace(".mp3", ".wav")
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(raw)
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav, "-codec:a", "libmp3lame",
         "-q:a", "4", out_mp3],
        check=True, capture_output=True, timeout=60,
    )


def _get_duration(file_path):
    """يرجع مدة الملف بالثواني."""
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


def generate_segment_audio(segment, out_path):
    """يولّد صوت مقطع واحد."""
    seg_type = segment["type"]
    text = segment.get("text", "").strip()
    if not text:
        return None

    voice = "Charon"
    print(f"   🔊 [{seg_type}] {text[:50]}...")
    try:
        _gemini_tts(text, out_path, voice=voice)
        duration = _get_duration(out_path)
        print(f"   ✅ {duration:.1f} ثانية")
        return duration
    except Exception as e:
        print(f"   ❌ فشل: {e}")
        return None


def build_full_audio(episode, out_path="out/episode-audio.mp3"):
    """يبني صوت الحلقة كاملة (بدون موسيقى)."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    segments = episode.get("segments", [])

    print(f"\n🎙️  بناء صوت الحلقة ({len(segments)} مقطع)")
    print("=" * 60)

    temp_dir = Path(tempfile.mkdtemp())
    segment_files = []

    for i, seg in enumerate(segments, 1):
        seg_type = seg["type"]
        text = seg.get("text", "").strip()

        if not text:
            print(f"   ⏭️  مقطع {i} ({seg_type}) بدون نص — متخطى")
            continue

        seg_file = temp_dir / f"seg-{i:02d}.mp3"
        duration = generate_segment_audio(seg, str(seg_file))
        if duration:
            segment_files.append(str(seg_file))

    if not segment_files:
        raise RuntimeError("لم يُولَّد أي صوت")

    print(f"\n🔗 دمج {len(segment_files)} مقطع...")
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
    print(f"\n✅ الحلقة الصوتية: {out_path}")
    print(f"   المدة: {duration/60:.1f} دقيقة")
    return out_path


if __name__ == "__main__":
    import json
    ep_file = "data/stories/yusuf/episode-01.json"
    episode = json.loads(Path(ep_file).read_text(encoding="utf-8"))
    build_full_audio(episode)
