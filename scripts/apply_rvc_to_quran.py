"""
apply_rvc_to_quran.py — تطبيق بصمة الصوت (clean.pth v4.0) على آيات القرآن فقط في حلقات القصص
- يقرأ: <yt_audio.mp3> <yt_meta.json> <out_yt_rvc.mp3>
- يُبقي فقرات السرد (Gemini) كما هي بدون RVC للحفاظ على نقائها.
- يحوّل آيات الحصري فقط ببصمة clean.pth مع هندسة الوضوح وصدى المسجد الهادئ،
  ويحدّث مدد _meta.json لتبقى الترجمة والرسم العثماني متطابقة تماماً مع الصوت.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
RVC_DIR = _REPO_ROOT / "rvc"
TARGET_SR = 48000
GAP_SEC = 0.20


def get_dur(path: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def run_rvc_cli(
    in_wav: Path,
    out_wav: Path,
    model_rel: str,
    index_rel: str,
    pitch: int,
    protect: float,
    index_rate: float,
    rms_mix: float,
):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(RVC_DIR)
    env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

    if out_wav.exists():
        out_wav.unlink()

    cmd = [
        sys.executable, "infer/cli.py",
        "--model", model_rel,
        "--input", str(in_wav.resolve()),
        "--output", str(out_wav.resolve()),
        "--index", index_rel,
        "--f0-method", "rmvpe",
        "--pitch", str(pitch),
        "--protect", str(protect),
        "--index-rate", str(index_rate),
        "--rms-mix-rate", str(rms_mix),
    ]
    res = subprocess.run(cmd, cwd=str(RVC_DIR), env=env, capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size < 1000:
        raise RuntimeError(f"❌ فشل RVC ({model_rel}): {res.stderr[-500:]}")


def _parse_my_pitch(raw_spec: str, audio_for_f0: Path) -> int:
    s = (raw_spec or "-2").strip().lower()
    if s == "auto":
        auto_pitch = -2
        try:
            import librosa
            import numpy as np
            y, _sr = librosa.load(str(audio_for_f0), sr=None)
            f0, voiced_flag, _ = librosa.pyin(y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"))
            f0_voiced = f0[voiced_flag]
            if len(f0_voiced) > 0:
                input_hz = float(np.median(f0_voiced))
                auto_pitch = round(12 * math.log2(125.0 / input_hz))
                print(f"   🎯 تحليل طبقة الصوت لبصمتي: {input_hz:.1f} Hz | Auto={auto_pitch}", flush=True)
        except Exception as e:
            print(f"   ⚠️ تعذر التحليل التلقائي ({e}) — استخدام -2", flush=True)
        return max(-7, min(5, auto_pitch))

    val = int(re.sub(r"[^0-9\-+]", "", s))
    return max(-12, min(12, val))


def convert_ayah_with_clean(in_48k: Path, out_wav: Path, pitch_spec: str = "-2"):
    clean_w = RVC_DIR / "assets" / "weights" / "clean.pth"
    if not clean_w.exists():
        raise RuntimeError(f"❌ ملف بصمتي clean.pth غير موجود في: {clean_w}")

    idx_arg = "clean.index" if (RVC_DIR / "clean.index").exists() else ""
    trimmed_in = out_wav.with_name(out_wav.stem + "_trim.wav")
    pre_cleaned = out_wav.with_name(out_wav.stem + "_pre.wav")
    rvc_raw = out_wav.with_name(out_wav.stem + "_raw.wav")
    dry_clean = out_wav.with_name(out_wav.stem + "_dry.wav")

    # إزالة الصمت الخلفي قبل المعالجة حتى لا يتمدد مع التبطيء أو الصدى
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(in_48k),
            "-af", "areverse,silenceremove=start_periods=1:start_duration=0:start_threshold=-46dB,areverse",
            "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le", str(trimmed_in),
        ],
        check=True, capture_output=True,
    )
    src_for_rvc = trimmed_in if get_dur(trimmed_in) >= 0.25 else in_48k

    tempo_slow = 0.75
    tempo_fast = 1.0 / tempo_slow
    pre_af = f"highpass=f=60,lowpass=f=15500,afftdn=nr=2:nf=-50,atempo={tempo_slow}"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src_for_rvc), "-af", pre_af,
            "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le", str(pre_cleaned),
        ],
        check=True, capture_output=True,
    )

    final_pitch = _parse_my_pitch(pitch_spec, pre_cleaned)
    print(f"   🎙️ تحويل الآية ببصمة clean.pth (pitch={final_pitch})...", flush=True)
    run_rvc_cli(pre_cleaned, rvc_raw, "assets/weights/clean.pth", idx_arg, final_pitch, 0.38, 0.80, 0.25)

    clarity_chain = (
        f"atempo={tempo_fast},"
        "highpass=f=75,lowpass=f=14500,afftdn=nf=-30,"
        "equalizer=f=240:t=q:w=1.5:g=-3.0,"
        "equalizer=f=1500:t=q:w=1.5:g=2.0,"
        "equalizer=f=3200:t=q:w=1.3:g=4.5,"
        "equalizer=f=5500:t=q:w=1.2:g=3.0,"
        "acompressor=threshold=0.089:ratio=3:attack=15:release=200,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(rvc_raw), "-af", clarity_chain,
            "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le", str(dry_clean),
        ],
        check=True, capture_output=True,
    )

    # صدى مسجد هادئ ومتزن للآية
    dry_dur = get_dur(dry_clean)
    echoed_tmp = out_wav.with_name(out_wav.stem + "_echo.wav")
    af_echo = (
        "apad=pad_dur=0.65,"
        "aecho=1.0:0.95:125|250:0.24|0.11,"
        "loudnorm=I=-15:TP=-1.5:LRA=10,alimiter=limit=0.95"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(dry_clean), "-af", af_echo,
            "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le", str(echoed_tmp),
        ],
        check=True, capture_output=True,
    )
    tot_dur = get_dur(echoed_tmp)
    tail_d = 0.50
    fade_start = max(dry_dur, tot_dur - tail_d)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(echoed_tmp),
            "-af", f"afade=t=out:st={fade_start:.3f}:d={tail_d:.3f},afade=t=in:st=0:d=0.015",
            "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav),
        ],
        check=True, capture_output=True,
    )


def apply_rvc_to_episode(in_mp3: str, meta_json: str, out_mp3: str):
    in_path = Path(in_mp3)
    meta_path = Path(meta_json)
    out_path = Path(out_mp3)

    if not in_path.exists():
        raise RuntimeError(f"❌ ملف الصوت غير موجود: {in_path}")
    if not meta_path.exists():
        raise RuntimeError(f"❌ ملف meta غير موجود: {meta_path}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    segments = meta.get("segments", [])
    pitch_spec = os.getenv("RVC_MY_PITCH", "-2").strip()

    work = Path(tempfile.mkdtemp(prefix="rvc_ep_"))
    try:
        # فك ترميز الملف مرة واحدة إلى WAV 48kHz لتفادي أي انحراف زمني بين المقاطع
        master_wav = work / "master_48k.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(in_path),
                "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le", str(master_wav),
            ],
            check=True, capture_output=True,
        )

        gap_wav = work / "gap.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono",
                "-t", str(GAP_SEC), "-c:a", "pcm_s16le", str(gap_wav),
            ],
            check=True, capture_output=True,
        )

        wav_parts: list[Path] = []
        cursor = 0.0
        converted_count = 0

        with wave.open(str(master_wav), "rb") as wf:
            total_frames = wf.getnframes()
            sampwidth = wf.getsampwidth()
            nchannels = wf.getnchannels()
            framerate = wf.getframerate()

            flat_chunks = [
                (seg_idx, seg, c_idx, chunk)
                for seg_idx, seg in enumerate(segments)
                for c_idx, chunk in enumerate(seg.get("chunks", []))
                if float(chunk.get("duration", 0)) > 0
            ]

            for pos, (seg_idx, seg, c_idx, chunk) in enumerate(flat_chunks):
                c_dur = float(chunk.get("duration", 0))
                start_f = min(total_frames, max(0, int(round(cursor * framerate))))
                if pos == len(flat_chunks) - 1:
                    end_f = total_frames
                else:
                    end_f = min(total_frames, max(start_f, int(round((cursor + c_dur) * framerate))))
                cursor += c_dur

                wf.setpos(start_f)
                raw_frames = wf.readframes(max(0, end_f - start_f))

                slice_wav = work / f"slice_{seg_idx}_{c_idx}.wav"
                with wave.open(str(slice_wav), "wb") as out_wf:
                    out_wf.setnchannels(nchannels)
                    out_wf.setsampwidth(sampwidth)
                    out_wf.setframerate(framerate)
                    out_wf.writeframes(raw_frames)

                if seg.get("type") == "quran" and get_dur(slice_wav) >= 0.35:
                    conv_wav = work / f"rvc_{seg_idx}_{c_idx}.wav"
                    convert_ayah_with_clean(slice_wav, conv_wav, pitch_spec=pitch_spec)
                    wav_parts.extend([conv_wav, gap_wav])
                    chunk["duration"] = round(get_dur(conv_wav) + GAP_SEC, 3)
                    converted_count += 1
                else:
                    wav_parts.append(slice_wav)

        if not wav_parts:
            raise RuntimeError("❌ لم يُعثر على مقاطع صالحة للدمج.")

        concat_txt = work / "concat_rvc.txt"
        concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in wav_parts), encoding="utf-8")

        merged_wav = work / "merged_rvc.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
                "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le", str(merged_wav),
            ],
            check=True, capture_output=True, timeout=300,
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(merged_wav),
                "-c:a", "libmp3lame", "-b:a", "128k", str(out_path),
            ],
            check=True, capture_output=True, timeout=300,
        )

        meta["total_duration"] = get_dur(out_path)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ تم تطبيق بصمة clean.pth على {converted_count} آية: {out_path.name} ({meta['total_duration']/60:.1f} دقيقة)", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    if len(sys.argv) < 4:
        print("الاستخدام: python apply_rvc_to_quran.py <yt_audio.mp3> <yt_meta.json> <out_yt_rvc.mp3>")
        sys.exit(1)
    apply_rvc_to_episode(sys.argv[1], sys.argv[2], sys.argv[3])


if __name__ == "__main__":
    main()
