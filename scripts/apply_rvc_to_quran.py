"""
apply_rvc_to_quran.py — تطبيق البصمة الصوتية (v4.0-clean + v3.1-myvoice) عبر infer/cli.py الرسمي
الإعدادات المطابقة لـ sudais_reverb.yml + رفع صوت التلاوة +30% مقارنة بالسرد:
  - clean (v4.0-clean)  : 87.5%  | pitch = -6
  - rough (v3.1-myvoice): 12.5%  | pitch = -4
  - الصدى: aecho=0.8:0.9:150|300|450:0.3|0.15|0.075 (مع ذيل صمت 0.6s لمنع البتر)
  - موازنة إلى I=-16 ثم رفع +30% (volume=1.30,alimiter=limit=0.95).

الاستخدام:
    python apply_rvc_to_quran.py <yt.mp3> <yt_meta.json> <output.mp3>
"""

import os
import re
import sys
import json
import shutil
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR  = _REPO_ROOT / "out" / "audio_cache"

RVC_LAYERS = [
    {"name": "clean", "path": "assets/weights/clean.pth", "index": "clean.index", "pitch": "-6", "weight": 0.875},
    {"name": "rough", "path": "assets/weights/rough.pth", "index": "rough.index", "pitch": "-4", "weight": 0.125},
]

ECHO_FILTER = "aecho=0.8:0.9:150|300|450:0.3|0.15|0.075"
TARGET_SR = "24000"
SILENCE_DUR = 0.20
RECITER_VOLUME_MULT = 1.30  # +30% مقارنة بالسرد


def get_dur(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def run_rvc_cli(in_48k: Path, out_wav: Path, layer: dict, rvc_dir: Path, env: dict):
    cmd = [
        sys.executable, "infer/cli.py",
        "--model", layer["path"],
        "--input", str(in_48k.resolve()),
        "--output", str(out_wav.resolve()),
        "--index", layer["index"],
        "--f0-method", "rmvpe",
        "--pitch", layer["pitch"],
        "--protect", "0.45",
        "--index-rate", "0.4",
        "--rms-mix-rate", "0.8",
    ]
    res = subprocess.run(cmd, cwd=str(rvc_dir), env=env, capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size < 1000:
        raise RuntimeError(
            f"فشل infer/cli.py ({layer['name']}): code={res.returncode}\n"
            f"STDOUT: {res.stdout[-400:]}\nSTDERR: {res.stderr[-400:]}"
        )


def mix_and_echo(clean_wav: Path, rough_wav: Path, out_wav: Path):
    """يمزج الطبقتين (87.5% / 12.5%) ويضيف الصدى ثم يرفع الصوت +30% فوق مستوى السرد."""
    filter_complex = (
        f"[0:a]volume={RVC_LAYERS[0]['weight']}[a0];"
        f"[1:a]volume={RVC_LAYERS[1]['weight']}[a1];"
        f"[a0][a1]amix=inputs=2:normalize=0,alimiter,"
        f"apad=pad_dur=0.6,{ECHO_FILTER},"
        f"areverse,silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB,areverse,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,volume={RECITER_VOLUME_MULT:.2f},alimiter=limit=0.95"
    )
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(clean_wav),
            "-i", str(rough_wav),
            "-filter_complex", filter_complex,
            "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le",
            str(out_wav),
        ],
        check=True, capture_output=True, timeout=120,
    )


def main():
    if len(sys.argv) < 4:
        print("الاستخدام: python apply_rvc_to_quran.py <yt.mp3> <yt_meta.json> <output.mp3>")
        sys.exit(1)

    yt_mp3 = Path(sys.argv[1]).resolve()
    meta_json = Path(sys.argv[2]).resolve()
    output_mp3 = Path(sys.argv[3]).resolve()
    rvc_dir = (_REPO_ROOT / "rvc").resolve()

    if not yt_mp3.exists() or not meta_json.exists():
        print("❌ ملف الصوت أو الميتاداتا غير موجود")
        sys.exit(1)

    if not (rvc_dir / "infer" / "cli.py").exists():
        print(f"❌ infer/cli.py غير موجود في {rvc_dir}")
        sys.exit(1)

    for layer in RVC_LAYERS:
        w_path = rvc_dir / layer["path"]
        if not w_path.exists():
            print(f"❌ وزن الموديل مفقود: {w_path}")
            sys.exit(1)

    meta = json.loads(meta_json.read_text(encoding="utf-8"))
    ep_stem = meta_json.stem[:-5] if meta_json.stem.endswith("_meta") else meta_json.stem
    ep_name = re.sub(r"^yt_", "", ep_stem)
    ep_cache = CACHE_DIR / ep_name

    temp_dir = (_REPO_ROOT / "temp_rvc_work").resolve()
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    silence_wav = temp_dir / "silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono",
         "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_wav)],
        check=True, capture_output=True,
    )

    print("=" * 65)
    print("🎙️ تطبيق البصمة v4.0-clean + v3.1-myvoice (+30% مستوى القارئ)")
    print("=" * 65)

    current_time = 0.0
    timeline_entries = []
    unique_ayahs: dict[str, dict] = {}

    for seg_idx, seg in enumerate(meta.get("segments", [])):
        is_quran = seg.get("type") == "quran"
        for c_idx, chunk in enumerate(seg.get("chunks", [])):
            dur = float(chunk.get("duration", 0))
            if is_quran:
                s = int(chunk.get("surah") or seg.get("surah") or 0)
                a = int(chunk.get("ayah") or 0)
                key = f"{s}_{a}" if (s and a) else f"seg{seg_idx}_{c_idx}"
                if key not in unique_ayahs:
                    cached_norm = ep_cache / f"yt_husary_{s}_{a}_norm.wav"
                    in_48k = temp_dir / f"in48_{key}.wav"
                    if cached_norm.exists():
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", str(cached_norm), "-ar", "48000", "-ac", "1", str(in_48k)],
                            check=True, capture_output=True,
                        )
                    else:
                        extract_dur = max(0.2, dur - SILENCE_DUR)
                        subprocess.run(
                            ["ffmpeg", "-y", "-ss", f"{current_time:.3f}", "-t", f"{extract_dur:.3f}",
                             "-i", str(yt_mp3), "-ar", "48000", "-ac", "1", str(in_48k)],
                            check=True, capture_output=True,
                        )
                    unique_ayahs[key] = {"key": key, "in_48k": in_48k}
                timeline_entries.append({"is_quran": True, "key": key, "chunk": chunk})
            else:
                narr_wav = temp_dir / f"narr_{seg_idx}_{c_idx}.wav"
                extract_dur = max(0.1, dur - SILENCE_DUR)
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{current_time:.3f}", "-t", f"{extract_dur:.3f}",
                     "-i", str(yt_mp3), "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(narr_wav)],
                    check=True, capture_output=True,
                )
                timeline_entries.append({"is_quran": False, "file": narr_wav, "chunk": chunk})
            current_time += dur

    env = os.environ.copy()
    env["PYTHONPATH"] = str(rvc_dir)
    env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

    final_ayah_wavs: dict[str, Path] = {}
    for item in unique_ayahs.values():
        k = item["key"]
        in_48k = item["in_48k"]
        print(f"🎵 معالجة الآية {k} بالبصمة v4.0-clean + v3.1-myvoice...", flush=True)

        clean_wav = temp_dir / f"clean_{k}.wav"
        rough_wav = temp_dir / f"rough_{k}.wav"
        run_rvc_cli(in_48k, clean_wav, RVC_LAYERS[0], rvc_dir, env)
        run_rvc_cli(in_48k, rough_wav, RVC_LAYERS[1], rvc_dir, env)

        out_wav = temp_dir / f"final_ayah_{k}.wav"
        mix_and_echo(clean_wav, rough_wav, out_wav)
        final_ayah_wavs[k] = out_wav
        print(f"   ✅ جاهز (+30%): {k}", flush=True)

    concat_files = []
    for entry in timeline_entries:
        wav_p = final_ayah_wavs[entry["key"]] if entry["is_quran"] else entry["file"]
        entry["chunk"]["duration"] = round(get_dur(wav_p) + SILENCE_DUR, 3)
        concat_files.extend([wav_p, silence_wav])

    concat_txt = temp_dir / "concat.txt"
    concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in concat_files), encoding="utf-8")

    merged_wav = temp_dir / "merged.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(merged_wav)],
        check=True, capture_output=True, timeout=300,
    )

    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(merged_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(output_mp3)],
        check=True, capture_output=True, timeout=300,
    )

    meta["total_duration"] = round(get_dur(output_mp3), 3)
    meta["rvc_applied"] = True
    meta["rvc_blend"] = "87.5% v4.0-clean (-6) | 12.5% v3.1-myvoice (-4)"
    meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"✅ اكتمل الصوت النهائي لليوتيوب: {output_mp3.name} ({meta['total_duration']/60:.1f} دقيقة)", flush=True)


if __name__ == "__main__":
    main()
