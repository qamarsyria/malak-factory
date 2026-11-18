"""
apply_rvc_to_quran.py — تطبيق البصمة الصوتية (v4.0-clean + v3.1-myvoice) على آيات الحصري فقط
الإعدادات المطابقة لـ sudais_reverb.yml:
  - clean (v4.0-clean)  : 87.5%  | pitch = -6
  - rough (v3.1-myvoice): 12.5%  | pitch = -4
  - الصدى: aecho=0.8:0.9:150|300|450:0.3|0.15|0.075 (مع ذيل صمت 0.6s لمنع البتر)
  - السرد (Gemini TTS) يبقى كما هو دون لمس.

الاستخدام:
    python apply_rvc_to_quran.py <yt.mp3> <yt_meta.json> <output.mp3>
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from scipy.io import wavfile

_REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR  = _REPO_ROOT / "out" / "audio_cache"

RVC_LAYERS = [
    {"name": "clean", "weight_file": "clean.pth", "index": "clean.index", "pitch": -6, "weight": 0.875},
    {"name": "rough", "weight_file": "rough.pth", "index": "rough.index", "pitch": -4, "weight": 0.125},
]

ECHO_FILTER = "aecho=0.8:0.9:150|300|450:0.3|0.15|0.075"
TARGET_SR = "24000"
SILENCE_DUR = 0.20


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


def init_rvc_engine(rvc_dir: Path):
    """تهيئة محرك RVC مرة واحدة فقط بدلاً من إعادة تحميله لكل آية."""
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    os.environ["weight_root"] = str(rvc_dir / "assets" / "weights")
    os.environ["index_root"] = str(rvc_dir)
    os.environ["rmvpe_root"] = str(rvc_dir / "assets" / "rmvpe")

    sys.path.insert(0, str(rvc_dir))
    old_cwd = os.getcwd()
    old_argv = sys.argv
    os.chdir(rvc_dir)
    sys.argv = [sys.argv[0]]
    try:
        from configs.config import Config
        from infer.modules.vc.modules import VC

        config = Config()
        config.device = "cpu"
        config.is_half = False
        vc = VC(config)
        return vc, old_cwd
    finally:
        sys.argv = old_argv


def run_layer_batch(vc, layer: dict, ayah_items: list[dict], rvc_dir: Path, temp_dir: Path) -> dict[str, Path]:
    """يحوّل قائمة آيات كاملة بموديل واحد دون إعادة تحميل الموديل."""
    print(f"🎙️ تحميل موديل {layer['name']} (pitch={layer['pitch']}, weight={layer['weight']})...", flush=True)
    vc.get_vc(layer["weight_file"])

    idx_path = str(rvc_dir / layer["index"])
    if not os.path.exists(idx_path):
        idx_path = ""

    outputs = {}
    for item in ayah_items:
        key = item["key"]
        in_48k = item["in_48k"]
        out_wav = temp_dir / f"{layer['name']}_{key}.wav"

        info, (tgt_sr, audio_opt) = vc.vc_single(
            sid=0,
            input_audio_path=str(in_48k),
            f0_up_key=int(layer["pitch"]),
            f0_file=None,
            f0_method="rmvpe",
            file_index=idx_path,
            file_index2="",
            index_rate=0.4,
            filter_radius=3,
            resample_sr=0,
            rms_mix_rate=0.7,
            protect=0.45,
        )
        if audio_opt is None:
            raise RuntimeError(f"فشل RVC ({layer['name']}) على {key}: {info}")
        wavfile.write(str(out_wav), tgt_sr, audio_opt)
        outputs[key] = out_wav
        print(f"   ✅ {layer['name']}: {key}", flush=True)
    return outputs


def mix_and_echo(clean_wav: Path, rough_wav: Path, out_wav: Path):
    """يمزج الطبقتين (87.5% / 12.5%) ويضيف الصدى مع ذيل صمت يمنع البتر."""
    filter_complex = (
        f"[0:a]volume={RVC_LAYERS[0]['weight']}[a0];"
        f"[1:a]volume={RVC_LAYERS[1]['weight']}[a1];"
        f"[a0][a1]amix=inputs=2:normalize=0,alimiter,"
        f"apad=pad_dur=0.6,{ECHO_FILTER},"
        f"areverse,silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB,areverse"
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

    for layer in RVC_LAYERS:
        w_path = rvc_dir / "assets" / "weights" / layer["weight_file"]
        if not w_path.exists():
            print(f"❌ وزن الموديل مفقود: {w_path}")
            sys.exit(1)

    meta = json.loads(meta_json.read_text(encoding="utf-8"))
    ep_name = meta_json.stem.replace("_meta", "").replace("yt_", "")
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
    print("🎙️ تطبيق البصمة v4.0-clean + v3.1-myvoice على آيات الحصري")
    print("=" * 65)

    # 1. تجهيز قائمة المقاطع والآيات (استخدام كاش WAV المباشر عند توفره)
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

    # 2. تشغيل طبقتي RVC على الآيات فقط
    ayah_list = list(unique_ayahs.values())
    final_ayah_wavs: dict[str, Path] = {}

    if ayah_list:
        vc, old_cwd = init_rvc_engine(rvc_dir)
        try:
            clean_out = run_layer_batch(vc, RVC_LAYERS[0], ayah_list, rvc_dir, temp_dir)
            rough_out = run_layer_batch(vc, RVC_LAYERS[1], ayah_list, rvc_dir, temp_dir)
        finally:
            os.chdir(old_cwd)

        print("🔬 مزج الطبقتين وتطبيق الصدى...", flush=True)
        for item in ayah_list:
            k = item["key"]
            out_wav = temp_dir / f"final_ayah_{k}.wav"
            mix_and_echo(clean_out[k], rough_out[k], out_wav)
            final_ayah_wavs[k] = out_wav

    # 3. إعادة تجميع الملف الصوتي وتحديث مدد الميتاداتا بدقة
    concat_files = []
    for entry in timeline_entries:
        if entry["is_quran"]:
            wav_p = final_ayah_wavs[entry["key"]]
        else:
            wav_p = entry["file"]
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
