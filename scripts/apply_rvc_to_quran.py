"""
scripts/test_rvc_clarity.py
اختبار سريع لـ 5 إعدادات RVC على آية واحدة لاختيار أنقى وضوح لحروف بصمتك.
"""
import os
import sys
import subprocess
from pathlib import Path
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
RVC_DIR = REPO_ROOT / "rvc"
OUT_DIR = REPO_ROOT / "out" / "rvc_clarity_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CLARITY_EQ = (
    "highpass=f=85,"
    "equalizer=f=220:t=q:w=1.2:g=-3.0,"
    "equalizer=f=3200:t=q:w=1.2:g=4.5,"
    "equalizer=f=5500:t=q:w=1.0:g=3.5"
)
ECHO = "aecho=0.8:0.9:150|300|450:0.25|0.12|0.06"


def download_ayah(reciter_id: str, surah: int, ayah: int, dst_wav: Path):
    mp3 = dst_wav.with_suffix(".mp3")
    if not mp3.exists():
        url = f"https://everyayah.com/data/{reciter_id}/{surah:03d}{ayah:03d}.mp3"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        mp3.write_bytes(r.content)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dst_wav)],
        check=True, capture_output=True,
    )


def run_rvc(in_wav: Path, out_wav: Path, pitch: int, index_rate: float = 0.75, protect: float = 0.33):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(RVC_DIR)
    env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    cmd = [
        sys.executable, "infer/cli.py",
        "--model", "assets/weights/clean.pth",
        "--input", str(in_wav.resolve()),
        "--output", str(out_wav.resolve()),
        "--index", "clean.index",
        "--f0-method", "rmvpe",
        "--pitch", str(pitch),
        "--protect", str(protect),
        "--index-rate", str(index_rate),
        "--rms-mix-rate", "0.5",
    ]
    subprocess.run(cmd, cwd=str(RVC_DIR), env=env, check=True)


def finalize(rvc_wav: Path, src_wav: Path | None, out_wav: Path, air_blend: float = 0.0):
    if src_wav and air_blend > 0:
        fc = (
            f"[0:a]{CLARITY_EQ}[v0];"
            f"[1:a]highpass=f=4200,volume={air_blend:.2f}[air];"
            f"[v0][air]amix=inputs=2:duration=first:normalize=0,"
            f"apad=pad_dur=0.6,{ECHO},"
            f"loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(rvc_wav), "-i", str(src_wav),
             "-filter_complex", fc, "-ar", "24000", "-ac", "1", str(out_wav)],
            check=True, capture_output=True,
        )
    else:
        af = f"{CLARITY_EQ},apad=pad_dur=0.6,{ECHO},loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(rvc_wav), "-af", af, "-ar", "24000", "-ac", "1", str(out_wav)],
            check=True, capture_output=True,
        )


def main():
    husary_48k = OUT_DIR / "src_husary_001007.wav"
    qatami_48k = OUT_DIR / "src_qatami_001007.wav"
    download_ayah("Husary_128kbps", 1, 7, husary_48k)
    download_ayah("Nasser_Alqatami_128kbps", 1, 7, qatami_48k)

    raw_h_p0 = OUT_DIR / "raw_h_p0.wav"
    raw_h_m2 = OUT_DIR / "raw_h_m2.wav"
    raw_q_m2 = OUT_DIR / "raw_q_m2.wav"

    run_rvc(husary_48k, raw_h_p0, pitch=0)
    run_rvc(husary_48k, raw_h_m2, pitch=-2)
    run_rvc(qatami_48k, raw_q_m2, pitch=-2)

    finalize(raw_h_p0, None,       OUT_DIR / "1_husary_p0_eq.wav",           air_blend=0.0)
    finalize(raw_h_m2, None,       OUT_DIR / "2_husary_m2_eq.wav",           air_blend=0.0)
    finalize(raw_h_p0, husary_48k, OUT_DIR / "3_husary_p0_air_clarity.wav",  air_blend=0.25)
    finalize(raw_h_m2, husary_48k, OUT_DIR / "4_husary_m2_air_clarity.wav",  air_blend=0.25)
    finalize(raw_q_m2, qatami_48k, OUT_DIR / "5_qatami_m2_air_clarity.wav",  air_blend=0.25)

    print(f"✅ تم توليد 5 عينات اختبار في: {OUT_DIR}")


if __name__ == "__main__":
    main()
