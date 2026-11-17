"""
apply_rvc_to_quran.py — تطبيق RVC على آيات YT فقط
يقرأ _yt.mp3 + _yt_meta.json، يستخرج مقاطع الآيات، يطبق RVC blend، ويعيد الدمج.

Blend (جديد):
  - clean.pth  : 90%  pitch -8
  - rough.pth  :  4%  pitch -4
  - malak.pth  :  6%  pitch +6
الصدى: aecho=0.8:0.85:100|200:0.15|0.08
التسريع: 1.20 للحصري، 1.05 لياسر

⚠️ السرد (Gemini TTS) لا يُلمس — فقط آيات القارئ البشري (الحصري) تُعالج.

الاستخدام:
    python apply_rvc_to_quran.py <yt.mp3> <yt_meta.json> <output.mp3>
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# إعدادات RVC
# ═══════════════════════════════════════════════════════════
RVC_MODELS = [
    {"name": "clean", "path": "assets/weights/clean.pth", "index": "clean.index", "pitch": "-8", "weight": 0.90},
    {"name": "rough", "path": "assets/weights/rough.pth", "index": "rough.index", "pitch": "-4", "weight": 0.04},
    {"name": "malak", "path": "assets/weights/malak.pth", "index": "malak.index", "pitch": "+6", "weight": 0.06},
]

ECHO_FILTER = "aecho=0.8:0.85:100|200:0.15|0.08"
TARGET_AR = "48000"
TEMP_DIR = "temp_chunks"

# ═══════════════════════════════════════════════════════════
# دوال مساعدة
# ═══════════════════════════════════════════════════════════
def get_dur(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def get_tempo(reciter: str) -> tuple[float, str]:
    """سرعة التسريع حسب القارئ."""
    if "Yasser" in reciter or "ياسر" in reciter:
        return 1.05, "5% (ياسر)"
    if "Husary" in reciter or "الحصري" in reciter:
        return 1.20, "20% (الحصري)"
    return 1.10, "10% (افتراضي)"


# ═══════════════════════════════════════════════════════════
# RVC على مقطع واحد
# ═══════════════════════════════════════════════════════════
def rvc_chunk(input_wav: Path, base_name: str, tempo: float, rvc_dir: str, env: dict, temp_dir: Path) -> Path | None:
    """
    يطبق RVC blend على مقطع واحد.
    يعيد مسار الملف النهائي (مع الصدى) أو None لو فشل.
    """
    try:
        # 1. RVC (3 موديلات)
        for m in RVC_MODELS:
            out_wav = temp_dir / f"rvc_{base_name}_{m['name']}.wav"
            subprocess.run(
                [sys.executable, "infer/cli.py",
                 "--model", m["path"],
                 "--input", str(input_wav),
                 "--output", str(out_wav.absolute()),
                 "--index", m["index"],
                 "--f0-method", "rmvpe",
                 "--pitch", m["pitch"],
                 "--protect", "0.4",
                 "--index-rate", "0.4",
                 "--rms-mix-rate", "0.8"],
                cwd=rvc_dir, env=env,
                check=True, capture_output=True, timeout=600
            )
            if not out_wav.exists() or out_wav.stat().st_size < 1000:
                raise RuntimeError(f"RVC {m['name']} أنتج ملفاً صغيراً")

        # 2. المزج بالأوزان الجديدة (90/4/6)
        mix_inputs = []
        for m in RVC_MODELS:
            mix_inputs.extend(["-i", str((temp_dir / f"rvc_{base_name}_{m['name']}.wav").absolute())])

        filter_parts = []
        for i, m in enumerate(RVC_MODELS):
            filter_parts.append(f"[{i}:a]volume={m['weight']}[a{i}]")
        mix_filter = "; ".join(filter_parts) + "; " + \
                     "".join(f"[a{i}]" for i in range(len(RVC_MODELS))) + \
                     f"amix=inputs={len(RVC_MODELS)}:normalize=0,alimiter"

        mixed_wav = temp_dir / f"mix_{base_name}.wav"
        subprocess.run(
            ["ffmpeg", "-y"] + mix_inputs +
            ["-filter_complex", mix_filter, str(mixed_wav.absolute())],
            check=True, capture_output=True, timeout=120
        )

        # 3. التسريع + الصدى
        echo_wav = temp_dir / f"final_{base_name}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mixed_wav.absolute()),
             "-af", f"atempo={tempo},{ECHO_FILTER}",
             "-ar", TARGET_AR, "-ac", "1",
             str(echo_wav.absolute())],
            check=True, capture_output=True, timeout=120
        )

        if not echo_wav.exists() or echo_wav.stat().st_size < 1000:
            raise RuntimeError("الصدى أنتج ملفاً صغيراً")

        return echo_wav

    except subprocess.CalledProcessError as e:
        print(f"      ❌ RVC subprocess فشل: {str(e.stderr)[:150]}")
        return None
    except Exception as e:
        print(f"      ❌ {type(e).__name__}: {str(e)[:150]}")
        return None


# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def main():
    if len(sys.argv) < 4:
        print("الاستخدام: python apply_rvc_to_quran.py <yt.mp3> <yt_meta.json> <output.mp3>")
        sys.exit(1)

    yt_mp3 = os.path.abspath(sys.argv[1])
    meta_json = os.path.abspath(sys.argv[2])
    output_mp3 = os.path.abspath(sys.argv[3])

    if not os.path.exists(yt_mp3):
        print(f"❌ الملف غير موجود: {yt_mp3}")
        sys.exit(1)
    if not os.path.exists(meta_json):
        print(f"❌ الميتاداتا غير موجودة: {meta_json}")
        sys.exit(1)

    print("=" * 60)
    print("🎙️ تطبيق RVC على الآيات")
    print("=" * 60)

    # 1. تحقق من الموديلات
    rvc_dir = os.path.abspath("rvc")
    for m in RVC_MODELS:
        model_path = os.path.join(rvc_dir, m["path"])
        if not os.path.exists(model_path):
            print(f"❌ موديل مفقود: {m['path']}")
            sys.exit(1)
    print(f"✅ 3 موديلات RVC جاهزة")

    # 2. تجهيز temp
    temp_dir = Path(TEMP_DIR)
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir()

    # 3. قراءة الميتاداتا
    meta = json.loads(Path(meta_json).read_text(encoding="utf-8"))
    segments = meta["segments"]

    # 4. استخراج المقاطع
    print("\n🔪 فصل المقاطع...")
    current_time = 0.0
    chunk_info = []

    for seg_idx, seg in enumerate(segments):
        is_quran = (seg["type"] == "quran")
        reciter = seg.get("reciter", "الشيخ محمود خليل الحصري")
        for c_idx, chunk in enumerate(seg["chunks"]):
            dur = chunk["duration"]
            chunk_file = temp_dir / f"seg_{seg_idx}_c{c_idx}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(current_time), "-t", str(dur),
                 "-i", yt_mp3, "-ar", TARGET_AR, "-ac", "1", str(chunk_file)],
                capture_output=True
            )
            current_time += dur
            chunk_info.append({
                "file": str(chunk_file.absolute()),
                "is_quran": is_quran,
                "duration": dur,
                "reciter": reciter,
            })

    print(f"   {len(chunk_info)} مقطع مستخرج")

    # 5. تطبيق RVC على الآيات
    print("\n🎙️ RVC على الآيات...")
    env = os.environ.copy()
    env["PYTHONPATH"] = rvc_dir

    processed = 0
    failed = 0
    for item in chunk_info:
        if not item["is_quran"]:
            continue  # السرد يُترك كما هو

        input_wav = Path(item["file"])
        base_name = input_wav.stem
        tempo, tempo_label = get_tempo(item["reciter"])

        print(f"   🎵 {base_name} - {tempo_label}")

        final_wav = rvc_chunk(input_wav, base_name, tempo, rvc_dir, env, temp_dir)

        if final_wav:
            item["file"] = str(final_wav)
            item["duration"] = get_dur(final_wav)
            processed += 1
            # تنظيف الملفات الوسيطة لهذا المقطع
            for m in RVC_MODELS:
                p = temp_dir / f"rvc_{base_name}_{m['name']}.wav"
                if p.exists():
                    p.unlink()
            mp = temp_dir / f"mix_{base_name}.wav"
            if mp.exists():
                mp.unlink()
            if input_wav.exists():
                input_wav.unlink()
        else:
            print(f"      ⚠️ فشل — استخدام الصوت الأصلي")
            failed += 1

    print(f"\n📊 نجح: {processed} | فشل: {failed}")

    # 6. الدمج النهائي
    print("\n🔗 الدمج...")
    concat_file = temp_dir / "final_concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{item['file']}'" for item in chunk_info),
        encoding="utf-8"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file),
         "-c:a", "libmp3lame", "-b:a", "192k",
         output_mp3],
        check=True, capture_output=True, timeout=600
    )

    # 7. تحديث الميتاداتا (المدد الجديدة)
    flat_idx = 0
    for seg in meta["segments"]:
        for chunk in seg.get("chunks", []):
            if flat_idx < len(chunk_info):
                chunk["duration"] = chunk_info[flat_idx]["duration"]
            flat_idx += 1

    meta["total_duration"] = sum(
        c["duration"] for seg in meta["segments"] for c in seg.get("chunks", [])
    )
    meta["rvc_applied"] = True
    meta["rvc_blend"] = "90% clean (-8) | 4% rough (-4) | 6% malak (+6)"
    Path(meta_json).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 8. تنظيف
    shutil.rmtree(temp_dir, ignore_errors=True)

    dur = get_dur(output_mp3)
    print(f"\n✅ اكتمل: {output_mp3}")
    print(f"   المدة: {dur/60:.1f} دقيقة | {processed} مقطع RVC")


if __name__ == "__main__":
    main()
