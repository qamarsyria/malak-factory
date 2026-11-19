#!/usr/bin/env python3
"""
malak_converter.py
==================
محوّل الصوت الثنائي ومهندس الاستوديو لمشروع «همسات ملك»:
- مقاطع "he"  -> 87.5% clean.pth + 12.5% rough.pth
- مقاطع "she" -> malak_voice.pth (+ added_malak_voice.index) مع SHE_PITCH=6 افتراضياً
  وإعدادات الكلام الطبيعي (protect=0.33, rms-mix-rate=0.25, index-rate=0.60, 44100Hz)
  + De-metallic EQ وماسترنغ ناعم + Sidechain Ducking مع الموسيقى.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

HE_PITCH_CLEAN = os.environ.get("HE_PITCH_CLEAN", "-6").strip()
HE_PITCH_ROUGH = os.environ.get("HE_PITCH_ROUGH", "-4").strip()
SHE_PITCH = os.environ.get("SHE_PITCH", "6").strip()
SHE_INDEX_RATE = os.environ.get("SHE_INDEX_RATE", "0.60").strip()

TARGET_SR = "44100"


def _find_rvc_dir() -> Path | None:
    for cand in [REPO_ROOT / "rvc", REPO_ROOT / "Retrieval-based-Voice-Conversion-WebUI"]:
        if (cand / "infer" / "cli.py").exists():
            return cand.resolve()
    return None


def _find_first_existing(candidates: list[Path]) -> Path | None:
    for p in candidates:
        if p.exists() and p.is_file() and p.stat().st_size > 1000:
            return p.resolve()
    return None


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


def _run_rvc_cli(
    in_48k: Path,
    out_wav: Path,
    model_path: Path,
    index_path: Path | None,
    pitch: str,
    index_rate: str,
    rvc_dir: Path,
    env: dict,
):
    cmd = [
        sys.executable, "infer/cli.py",
        "--model", str(model_path),
        "--input", str(in_48k.resolve()),
        "--output", str(out_wav.resolve()),
        "--f0-method", "rmvpe",
        "--pitch", str(pitch),
        "--protect", "0.33",
        "--rms-mix-rate", "0.25",
    ]
    if index_path and index_path.exists() and float(index_rate) > 0:
        cmd.extend(["--index", str(index_path), "--index-rate", str(index_rate)])
    else:
        cmd.extend(["--index-rate", "0"])

    res = subprocess.run(cmd, cwd=str(rvc_dir), env=env, capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size < 1000:
        raise RuntimeError(
            f"❌ فشل RVC ({model_path.name}): code={res.returncode}\n"
            f"STDERR: {res.stderr[-500:]}"
        )


def _prep_48k_for_rvc(src: Path, dst_48k: Path):
    af = "highpass=f=85,lowpass=f=11000,loudnorm=I=-18:TP=-2.0:LRA=9"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-af", af,
         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dst_48k)],
        check=True, capture_output=True, timeout=60,
    )


def _master_voice_segment(raw_wav: Path, out_wav: Path, is_dua: bool = False):
    reverb = "aecho=0.8:0.88:85|170:0.20|0.09" if is_dua else "aecho=0.8:0.88:48|98:0.15|0.06"
    af = (
        "highpass=f=80,"
        "lowpass=f=9500,"
        "equalizer=f=190:width_type=o:width=1.2:g=2.5,"
        "equalizer=f=2400:width_type=o:width=0.9:g=-2.6,"
        "equalizer=f=4200:width_type=o:width=0.9:g=-2.5,"
        "equalizer=f=6800:width_type=o:width=1.1:g=-2.0,"
        "acompressor=threshold=-20dB:ratio=2.3:attack=18:release=200,"
        f"apad=pad_dur=0.25,{reverb},"
        "areverse,silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB,areverse,"
        "loudnorm=I=-15:TP=-1.5:LRA=9,alimiter=limit=0.95"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw_wav), "-af", af,
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
        check=True, capture_output=True, timeout=90,
    )


def _mix_with_sidechain(voice_wav: Path, bg_wav: Path, out_mp3: Path, total_dur: float):
    fade_out_st = max(0.5, total_dur - 2.5)
    fc = (
        f"[0:a]apad=pad_dur=1.2[v_pad];"
        f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{total_dur + 1.2:.2f},"
        f"aresample={TARGET_SR},lowpass=f=4500,volume=0.26,afade=t=in:st=0:d=1.8,"
        f"afade=t=out:st={fade_out_st:.2f}:d=2.2[bg];"
        f"[v_pad]asplit=2[v_main][v_sc];"
        f"[bg][v_sc]sidechaincompress=threshold=0.025:ratio=4.5:attack=25:release=450[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[out]"
    )
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(voice_wav), "-i", str(bg_wav),
                "-filter_complex", fc, "-map", "[out]",
                "-ar", TARGET_SR, "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3),
            ],
            check=True, capture_output=True, timeout=180,
        )
    except Exception:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(voice_wav), "-i", str(bg_wav),
                "-filter_complex",
                f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{total_dur + 1.0:.2f},aresample={TARGET_SR},volume=0.16[bg];"
                "[0:a][bg]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95",
                "-ar", TARGET_SR, "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3),
            ],
            check=True, capture_output=True, timeout=180,
        )


def convert_episode(in_mp3: str, out_mp3: str):
    in_p = Path(in_mp3).resolve()
    out_p = Path(out_mp3).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    meta_path = in_p.with_name(f"{in_p.stem}_meta.json")
    rvc_dir = _find_rvc_dir()

    search_roots = [REPO_ROOT]
    if rvc_dir:
        search_roots.extend([rvc_dir, rvc_dir / "assets" / "weights", rvc_dir / "assets" / "indices"])

    clean_pth = _find_first_existing([d / "clean.pth" for d in search_roots])
    clean_idx = _find_first_existing([d / "clean.index" for d in search_roots])
    rough_pth = _find_first_existing([d / "rough.pth" for d in search_roots])
    rough_idx = _find_first_existing([d / "rough.index" for d in search_roots])

    malak_pth = _find_first_existing([d / "malak_voice.pth" for d in search_roots])
    malak_idx = _find_first_existing([d / "added_malak_voice.index" for d in search_roots])

    print(f"🔎 RVC Dir: {rvc_dir or 'غير موجود'}", flush=True)
    print(f"   • صوتك (clean/rough): {'✅' if clean_pth else '⚠️ مفقود'} | {'✅' if rough_pth else '⚠️ مفقود'}", flush=True)
    print(f"   • صوتها (malak_voice): {'✅' if malak_pth else '⚠️ مفقود'} | pitch={SHE_PITCH} | index={SHE_INDEX_RATE}", flush=True)

    env = os.environ.copy()
    if rvc_dir:
        env["PYTHONPATH"] = str(rvc_dir)
        env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

    if not meta_path.exists():
        work = out_p.parent / f"_conv_fallback_{in_p.stem}"
        work.mkdir(parents=True, exist_ok=True)
        in48 = work / "in48.wav"
        _prep_48k_for_rvc(in_p, in48)
        conv = work / "conv.wav"
        if rvc_dir and malak_pth:
            _run_rvc_cli(in48, conv, malak_pth, malak_idx, SHE_PITCH, SHE_INDEX_RATE, rvc_dir, env)
        else:
            shutil.copy2(in48, conv)
        mastered = work / "mastered.wav"
        _master_voice_segment(conv, mastered)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mastered), "-c:a", "libmp3lame", "-b:a", "192k", str(out_p)],
            check=True, capture_output=True,
        )
        shutil.rmtree(work, ignore_errors=True)
        return

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    work_dir = Path(meta.get("work_dir") or (out_p.parent / "audio_work" / in_p.stem))
    work_dir.mkdir(parents=True, exist_ok=True)
    is_dua = meta.get("theme_id") == "prayer_niqab"

    converted_wavs: list[Path] = []
    timeline_cursor = 0.0
    updated_segments = []

    segs = meta.get("segments") or []
    for idx, seg in enumerate(segs):
        dry_wav = Path(seg["dry_wav"])
        speaker = seg.get("speaker", "he")
        pause_ms = int(seg.get("pause_after_ms") or 450)

        in48 = work_dir / f"seg_{idx:03d}_48k.wav"
        _prep_48k_for_rvc(dry_wav, in48)
        raw_conv = work_dir / f"seg_{idx:03d}_rvc.wav"

        if rvc_dir and speaker == "he" and clean_pth:
            print(f"🎙️ [{idx+1}/{len(segs)}] تحويل بصوتك (87.5% clean + 12.5% rough)...", flush=True)
            c_wav = work_dir / f"seg_{idx:03d}_clean.wav"
            _run_rvc_cli(in48, c_wav, clean_pth, clean_idx, HE_PITCH_CLEAN, "0.45", rvc_dir, env)
            if rough_pth:
                r_wav = work_dir / f"seg_{idx:03d}_rough.wav"
                _run_rvc_cli(in48, r_wav, rough_pth, rough_idx, HE_PITCH_ROUGH, "0.45", rvc_dir, env)
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(c_wav), "-i", str(r_wav),
                        "-filter_complex", "[0:a]volume=0.875[a0];[1:a]volume=0.125[a1];[a0][a1]amix=inputs=2:normalize=0,alimiter",
                        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(raw_conv),
                    ],
                    check=True, capture_output=True, timeout=60,
                )
            else:
                shutil.copy2(c_wav, raw_conv)

        elif rvc_dir and speaker == "she" and malak_pth:
            print(f"🎙️ [{idx+1}/{len(segs)}] تحويل بصوت ملك (malak_voice | pitch={SHE_PITCH})...", flush=True)
            _run_rvc_cli(in48, raw_conv, malak_pth, malak_idx, SHE_PITCH, SHE_INDEX_RATE, rvc_dir, env)

        else:
            print(f"   ⚠️ تخطي RVC للمقطع {idx+1} ({speaker}) — استخدام الماسترنغ المباشر.", flush=True)
            shutil.copy2(in48, raw_conv)

        mastered_seg = work_dir / f"seg_{idx:03d}_master.wav"
        _master_voice_segment(raw_conv, mastered_seg, is_dua=is_dua)

        dur = _get_dur(mastered_seg)
        start_sec = round(timeline_cursor, 3)
        end_sec = round(timeline_cursor + dur, 3)

        seg_out = dict(seg)
        seg_out["final_wav"] = str(mastered_seg.resolve())
        seg_out["start_sec"] = start_sec
        seg_out["end_sec"] = end_sec
        seg_out["duration"] = dur
        updated_segments.append(seg_out)

        converted_wavs.append(mastered_seg)
        timeline_cursor = end_sec

        if idx < len(segs) - 1 and pause_ms > 0:
            sil_p = work_dir / f"pause_{idx:03d}.wav"
            sil_s = pause_ms / 1000.0
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono",
                 "-t", f"{sil_s:.3f}", "-c:a", "pcm_s16le", str(sil_p)],
                check=True, capture_output=True,
            )
            converted_wavs.append(sil_p)
            timeline_cursor += sil_s

    concat_txt = work_dir / "final_concat.txt"
    concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in converted_wavs), encoding="utf-8")
    voice_master_wav = work_dir / "voice_master_all.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(voice_master_wav)],
        check=True, capture_output=True, timeout=120,
    )

    bg_wav = Path(meta.get("bg_music_wav", ""))
    if bg_wav.exists():
        print("🎼 مزج الموسيقى مع Sidechain Ducking...", flush=True)
        _mix_with_sidechain(voice_master_wav, bg_wav, out_p, timeline_cursor)
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(voice_master_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(out_p)],
            check=True, capture_output=True,
        )

    final_total = _get_dur(out_p)
    meta["segments"] = updated_segments
    meta["final_mp3"] = str(out_p)
    meta["total_duration"] = final_total
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    out_meta = out_p.with_name(f"{out_p.stem}_meta.json")
    out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ اكتمل الصوت النهائي: {out_p.name} ({final_total:.1f} ثانية)", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python malak_converter.py <in.mp3> <out_malak.mp3>")
        sys.exit(1)
    convert_episode(sys.argv[1], sys.argv[2])
