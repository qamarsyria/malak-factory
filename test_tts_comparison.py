"""test_tts_comparison.py — مقارنة SILMA v1 vs Fish Speech 1.5 على آيات قرآنية.

لا يعدّل أي كود موجود. كل المخرجات في out/test_tts/.

الاستخدام:
    python test_tts_comparison.py

المخرجات:
    out/test_tts/
    ├── silma/              # 3 آيات بـ SILMA
    ├── fish/               # 3 آيات بـ Fish (إن كان مثبتاً)
    ├── alternated.mp3      # نسخة التناوب (SILMA → Fish → SILMA)
    └── report.txt          # تقرير المقارنة
"""

import os
import sys
import subprocess
import time
import traceback
from pathlib import Path

import requests


# ═════════════════════════════════════════════════════════════
# الإعدادات
# ═════════════════════════════════════════════════════════════
OUT_DIR = Path("out/test_tts")
REF_DIR = Path("assets/audio/reference")
REF_SILMA = REF_DIR / "test_silma_ref.wav"

TEST_AYAHS = [
    {"surah": 1, "ayah": 1, "text": "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ", "name": "الفاتحة:1"},
    {"surah": 1, "ayah": 2, "text": "الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ", "name": "الفاتحة:2"},
    {"surah": 112, "ayah": 1, "text": "قُلْ هُوَ اللَّهُ أَحَدٌ", "name": "الإخلاص:1"},
]

SAFE_RECITER = "Husary_128kbps"
EVERYAYAH = "https://everyayah.com/data"

ALTERNATION_PATTERN = ["silma", "fish", "silma"]  # ← التناوب الذي تريده


def log(msg):
    print(msg, flush=True)


# ═════════════════════════════════════════════════════════════
# أدوات مساعدة
# ═════════════════════════════════════════════════════════════
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


def get_size(path):
    return Path(path).stat().st_size if Path(path).exists() else 0


def get_peak_volume(path):
    """قياس الذروة بالـ dB — للكشف عن الملفات الصامتة."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        for line in r.stderr.split("\n"):
            if "max_volume" in line:
                return line.split("max_volume:")[1].strip()
    except Exception:
        pass
    return "?"


# ═════════════════════════════════════════════════════════════
# مرجع SILMA
# ═════════════════════════════════════════════════════════════
def download_silma_reference():
    REF_DIR.mkdir(parents=True, exist_ok=True)
    if REF_SILMA.exists() and REF_SILMA.stat().st_size > 10000:
        log(f"✅ مرجع SILMA موجود: {REF_SILMA}")
        return REF_SILMA

    filename = f"{1:03d}{1:03d}.mp3"
    url = f"{EVERYAYAH}/{SAFE_RECITER}/{filename}"
    log(f"⬇️ تحميل مرجع SILMA: {url}")

    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            log(f"❌ HTTP {r.status_code}")
            return None
        temp = REF_DIR / "temp_silma.mp3"
        temp.write_bytes(r.content)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(temp), "-ar", "22050", "-ac", "1",
             "-c:a", "pcm_s16le", str(REF_SILMA)],
            check=True, capture_output=True, timeout=60,
        )
        temp.unlink(missing_ok=True)
        log(f"✅ مرجع SILMA: {REF_SILMA.name} ({get_size(REF_SILMA)/1024:.1f} KB)")
        return REF_SILMA
    except Exception as e:
        log(f"❌ فشل التحميل: {e}")
        return None


# ═════════════════════════════════════════════════════════════
# SILMA
# ═════════════════════════════════════════════════════════════
def test_silma(ref_path):
    log("\n" + "=" * 60)
    log("🔊 اختبار SILMA")
    log("=" * 60)

    try:
        from silma_tts.api import SilmaTTS
    except ImportError:
        log("❌ SILMA غير مثبت: pip install silma-tts")
        return {}

    try:
        from quranic_phonemizer import Phonemizer
        phonemizer = Phonemizer()
        log("✅ Phonemizer محمّل")
    except Exception as e:
        log(f"⚠️ Phonemizer فشل: {e} — سيُستخدم النص مباشرة")
        phonemizer = None

    try:
        engine = SilmaTTS()
        log("✅ SILMA محمّل")
    except Exception as e:
        log(f"❌ فشل تحميل SILMA: {e}")
        return {}

    silma_dir = OUT_DIR / "silma"
    silma_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    for i, ayah in enumerate(TEST_AYAHS, 1):
        log(f"\n🎙️ [{i}/{len(TEST_AYAHS)}] {ayah['name']}: {ayah['text'][:40]}...")

        # phonemize — نحاول صيغتين (مرجع "1:1" أو النص العربي)
        gen_text = ayah["text"]
        if phonemizer:
            try:
                ph = phonemizer.phonemize(f"{ayah['surah']}:{ayah['ayah']}")
                extracted = _extract_phonemes(ph)
                if extracted and len(extracted) > 3:
                    gen_text = extracted
                    log(f"   phonemes (مرجع): {extracted[:60]}...")
            except Exception:
                try:
                    ph = phonemizer.phonemize(ayah["text"])
                    extracted = _extract_phonemes(ph)
                    if extracted and len(extracted) > 3:
                        gen_text = extracted
                        log(f"   phonemes (نص): {extracted[:60]}...")
                except Exception as e2:
                    log(f"   ⚠️ phonemizer: {str(e2)[:80]}")

        out = silma_dir / f"silma_{i}.wav"
        try:
            t0 = time.time()
            engine.infer(
                ref_file=str(ref_path),
                ref_text="بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ",  # ← الإصلاح الحرج
                gen_text=gen_text,
                file_wave=str(out),
            )
            elapsed = time.time() - t0

            dur = get_duration(out)
            size = get_size(out)
            peak = get_peak_volume(out)

            if dur > 0.3 and size > 5000:
                log(f"   ✅ {dur:.1f}s | {size/1024:.1f} KB | ذروة={peak} | {elapsed:.1f}s")
                results[ayah["name"]] = {
                    "status": "✅", "duration": dur, "size": size,
                    "peak": peak, "time": round(elapsed, 1),
                    "path": str(out),
                }
            else:
                log(f"   ❌ ملف تالف (dur={dur}, size={size})")
                results[ayah["name"]] = {"status": "❌", "reason": "ملف تالف"}
        except Exception as e:
            log(f"   ❌ {type(e).__name__}: {str(e)[:100]}")
            results[ayah["name"]] = {"status": "❌", "reason": str(e)[:80]}

    return results


def _extract_phonemes(ph):
    """استخراج phonemes من ناتج Phonemizer بأي صيغة."""
    if isinstance(ph, str):
        return ph
    if hasattr(ph, "phonemes"):
        v = ph.phonemes
        return " ".join(v()) if callable(v) else " ".join(v)
    if hasattr(ph, "text"):
        return str(ph.text)
    if hasattr(ph, "arabic_text"):
        return str(ph.arabic_text)
    return str(ph)


# ═════════════════════════════════════════════════════════════
# Fish Speech
# ═════════════════════════════════════════════════════════════
def _find_fish_api():
    """البحث عن API Fish Speech في أي إصدار."""
    try:
        r = subprocess.run(["fish-speech", "--help"], capture_output=True, timeout=10)
        if r.returncode == 0:
            return "cli"
    except Exception:
        pass
    try:
        from fish_speech.inference_engine import TTSInferenceEngine  # noqa
        return "python_v2"
    except ImportError:
        pass
    try:
        from fish_speech import TTS  # noqa
        return "python_v1"
    except ImportError:
        pass
    return None


def test_fish(ref_path):
    log("\n" + "=" * 60)
    log("🐟 اختبار Fish Speech")
    log("=" * 60)

    api = _find_fish_api()
    if not api:
        log("❌ Fish Speech غير مثبت")
        log("   تثبيت: pip install fish-speech")
        log("   (سيتخطى الاختبار ويكمل بـ SILMA فقط)")
        return {}

    log(f"✅ Fish API: {api}")

    if not ref_path or not ref_path.exists():
        log("❌ لا مرجع Fish")
        return {}

    fish_dir = OUT_DIR / "fish"
    fish_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    for i, ayah in enumerate(TEST_AYAHS, 1):
        log(f"\n🎙️ [{i}/{len(TEST_AYAHS)}] {ayah['name']}: {ayah['text'][:40]}...")

        out = fish_dir / f"fish_{i}.wav"
        try:
            t0 = time.time()
            success = _run_fish(api, ayah["text"], ref_path, out)
            elapsed = time.time() - t0

            if success and out.exists():
                dur = get_duration(out)
                size = get_size(out)
                peak = get_peak_volume(out)
                if dur > 0.3 and size > 5000:
                    log(f"   ✅ {dur:.1f}s | {size/1024:.1f} KB | ذروة={peak} | {elapsed:.1f}s")
                    results[ayah["name"]] = {
                        "status": "✅", "duration": dur, "size": size,
                        "peak": peak, "time": round(elapsed, 1),
                        "path": str(out),
                    }
                else:
                    log(f"   ❌ ملف تالف")
                    results[ayah["name"]] = {"status": "❌", "reason": "تالف"}
            else:
                log(f"   ❌ لم يُنتج ملفاً")
                results[ayah["name"]] = {"status": "❌", "reason": "لم يُنتج"}
        except Exception as e:
            log(f"   ❌ {type(e).__name__}: {str(e)[:100]}")
            results[ayah["name"]] = {"status": "❌", "reason": str(e)[:80]}

    return results


def _run_fish(api, text, ref_path, out_path):
    if api == "cli":
        r = subprocess.run(
            ["fish-speech", "tts", "--text", text,
             "--reference-audio", str(ref_path), "--output", str(out_path)],
            capture_output=True, timeout=180,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "ignore")[:200])
        return True

    if api == "python_v2":
        from fish_speech.inference_engine import TTSInferenceEngine
        from fish_speech.utils.schema import ServeTTSRequest
        engine = TTSInferenceEngine()
        req = ServeTTSRequest(
            text=text,
            references=[{"audio": str(ref_path),
                         "text": "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ"}],
        )
        for result in engine.inference(req):
            if result.code == "final":
                import soundfile as sf
                sf.write(str(out_path), result.audio, result.sample_rate)
                return True
        return False

    if api == "python_v1":
        from fish_speech import TTS
        TTS().tts(text=text, reference_audio=str(ref_path), output=str(out_path))
        return Path(out_path).exists()

    return False


# ═════════════════════════════════════════════════════════════
# بناء نسخة التناوب
# ═════════════════════════════════════════════════════════════
def _normalize(src, dst):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ar", "44100", "-ac", "1",
         "-c:a", "pcm_s16le", str(dst)],
        check=True, capture_output=True, timeout=120,
    )


def build_alternated(silma_results, fish_results):
    log("\n" + "=" * 60)
    log("🔀 بناء نسخة التناوب")
    log("=" * 60)

    parts = []
    for i, ayah in enumerate(TEST_AYAHS):
        engine = ALTERNATION_PATTERN[i]
        primary = silma_results if engine == "silma" else fish_results
        fallback = fish_results if engine == "silma" else silma_results

        if ayah["name"] in primary and primary[ayah["name"]].get("status") == "✅":
            parts.append((engine, primary[ayah["name"]]["path"]))
            log(f"   [{i+1}] {ayah['name']} → {engine} ✅")
        elif ayah["name"] in fallback and fallback[ayah["name"]].get("status") == "✅":
            parts.append((engine, fallback[ayah["name"]]["path"]))
            other = "fish" if engine == "silma" else "silma"
            log(f"   [{i+1}] {ayah['name']} → {engine} ⚠️ (fallback من {other})")
        else:
            log(f"   [{i+1}] {ayah['name']} → ❌ تخطي")

    if not parts:
        log("❌ لا مقاطع للتناوب")
        return None

    # توحيد الصيغة
    norm_dir = OUT_DIR / "_norm"
    norm_dir.mkdir(exist_ok=True)
    norm_parts = []
    for idx, (eng, path) in enumerate(parts):
        dst = norm_dir / f"part_{idx}.wav"
        _normalize(path, dst)
        norm_parts.append((eng, dst))

    # صمت 0.5s
    silent = OUT_DIR / "_silent.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "0.5", str(silent)],
        check=True, capture_output=True,
    )

    # concat
    concat_file = OUT_DIR / "_concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for idx, (_, path) in enumerate(norm_parts):
            f.write(f"file '{Path(path).resolve()}'\n")
            if idx < len(norm_parts) - 1:
                f.write(f"file '{silent.resolve()}'\n")

    out_mp3 = OUT_DIR / "alternated.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-ar", "44100", "-ac", "1",
         "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)],
        check=True, capture_output=True, timeout=120,
    )

    log(f"\n✅ {out_mp3} ({get_duration(out_mp3):.1f}s)")
    return out_mp3


# ═════════════════════════════════════════════════════════════
# التقرير
# ═════════════════════════════════════════════════════════════
def write_report(silma_results, fish_results, alt_file):
    report = OUT_DIR / "report.txt"
    lines = [
        "=" * 70,
        "📊 تقرير مقارنة TTS — SILMA vs Fish Speech",
        "=" * 70, "",
        f"{'الآية':<20} {'SILMA':<22} {'Fish Speech':<22}",
        "-" * 66,
    ]
    for ayah in TEST_AYAHS:
        name = ayah["name"]
        s = silma_results.get(name, {})
        f = fish_results.get(name, {})
        s_str = f"✅ {s['duration']:.1f}s" if s.get("status") == "✅" else "❌"
        f_str = f"✅ {f['duration']:.1f}s" if f.get("status") == "✅" else "❌"
        lines.append(f"{name:<20} {s_str:<22} {f_str:<22}")

    lines.append("")
    if alt_file:
        lines.append(f"🔀 نسخة التناوب: {alt_file}")
        lines.append(f"   المدة: {get_duration(alt_file):.1f}s")
        lines.append("")

    lines.append("📁 الملفات:")
    for f in sorted(OUT_DIR.rglob("*")):
        if f.is_file() and f.suffix in (".wav", ".mp3"):
            if f.name.startswith("_"):
                continue
            lines.append(f"   {f.relative_to(OUT_DIR)} ({get_size(f)/1024:.1f} KB)")

    text = "\n".join(lines)
    report.write_text(text, encoding="utf-8")
    print("\n" + text)


# ═════════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════════
def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 60)
    log("🧪 اختبار SILMA vs Fish Speech")
    log("=" * 60)
    log(f"الآيات: {len(TEST_AYAHS)}")
    log(f"المخرجات: {OUT_DIR}")

    ref = download_silma_reference()
    if not ref:
        log("❌ لا يمكن المتابعة بدون مرجع SILMA")
        sys.exit(1)

    # 1. SILMA
    silma_results = test_silma(ref)

    # 2. مرجع Fish من أول ناتج SILMA (نسخة AI → AI، بدون صوت بشري)
    fish_ref = ref
    for res in silma_results.values():
        if res.get("status") == "✅":
            fish_ref = Path(res["path"])
            log(f"\n📋 مرجع Fish = ناتج SILMA (AI → AI، بدون صوت بشري)")
            break

    # 3. Fish
    fish_results = test_fish(fish_ref)

    # 4. التناوب
    alt = build_alternated(silma_results, fish_results)

    # 5. التقرير
    write_report(silma_results, fish_results, alt)

    # 6. خلاصة
    s_ok = sum(1 for r in silma_results.values() if r.get("status") == "✅")
    f_ok = sum(1 for r in fish_results.values() if r.get("status") == "✅")
    log("\n" + "=" * 60)
    log(f"النتيجة: SILMA {s_ok}/{len(TEST_AYAHS)} | Fish {f_ok}/{len(TEST_AYAHS)}")
    log("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"\n❌ فشل: {e}")
        traceback.print_exc()
        sys.exit(1)
