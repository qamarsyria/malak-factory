"""توليد عينات صوتية بـ SILMA TTS (بدون تسجيل، بدون أرقام) لتدريب RVC.
يستخدم النصوص المشكولة مسبقاً من مكتباتك لضمان نطق سليم.
"""
import os, sys, gzip, json, subprocess, time, re
from pathlib import Path
from importlib.resources import files

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUTPUT_DIR = _REPO_ROOT / "rvc_training" / "silma_samples"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LIBRARIES = _REPO_ROOT / "data" / "libraries"

TARGET_COUNT = 100

# النص المرافق للعينة المرجعية المدمجة مع SILMA
REF_TEXT = "ويدقق النظر في القرآن الكريم وسائر الكتب السماوية ويتبع مسالك الرسل العظام عليهم الصلاة والسلام."


def get_ref_audio_path() -> str:
    """إرجاع مسار العينة المرجعية المدمجة داخل حزمة silma_tts."""
    try:
        # الطريقة الحديثة والآمنة للوصول للموارد داخل الحزمة
        ref_path = files("silma_tts").joinpath("infer/ref_audio_samples/ar.ref.24k.wav")
        # بعض إصدارات Python/الحزم تحتاج تحويلها إلى مسار فعلي
        if hasattr(ref_path, "as_file"):
            # as_file يُستخدم كـ context manager، لكننا نحتاج المسار الآن
            # لذا نستخدم __fspath__ مباشرة إن أمكن
            return str(ref_path)
        return str(ref_path)
    except Exception as e:
        print(f"⚠️ لم أستطع الوصول للعينة المدمجة: {e}")
        # مسار احتياطي شائع عند التثبيت من المصدر
        fallback = Path("/root/silma-tts/src/silma_tts/infer/ref_audio_samples/ar.ref.24k.wav")
        if fallback.exists():
            return str(fallback)
        raise FileNotFoundError("لم يتم العثور على العينة المرجعية ar.ref.24k.wav") from e


def load_vocalized_texts():
    texts = []
    # 1. آيات قرآنية (مشكولة 100%)
    p = LIBRARIES / "ayah-library.json.gz"
    if p.exists():
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                data = json.load(f)
                for item in data[:40]:
                    t = item.get("text", "").strip()
                    if 30 < len(t) < 250:
                        texts.append(t)
        except Exception as e:
            print(f"⚠️ آيات: {e}")

    # 2. أحاديث (غالباً مشكولة)
    p = LIBRARIES / "hadith-library.json.gz"
    if p.exists():
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                data = json.load(f)
                for item in data[:30]:
                    t = item.get("text", "").strip()
                    if 30 < len(t) < 300:
                        texts.append(t)
        except Exception as e:
            print(f"⚠️ أحاديث: {e}")

    # 3. نصوص عامة مشكولة يدوياً
    general = [
        "السَّلَامُ عَلَيْكُمْ وَرَحْمَةُ اللَّهِ وَبَرَكَاتُهُ.",
        "الصَّبْرُ مِفْتَاحُ الْفَرَجِ، وَالتَّوَكُّلُ عَلَى اللَّهِ سَبِيلُ النَّجَاةِ.",
        "اللَّهُمَّ ارْزُقْنَا الْعِلْمَ النَّافِعَ وَالْعَمَلَ الصَّالِحَ.",
        "مَنْ سَلَكَ طَرِيقاً يَطْلُبُ فِيهِ عِلْماً سَلَكَ اللَّهُ بِهِ طَرِيقاً إِلَى الْجَنَّةِ.",
        "اللَّهُمَّ صَلِّ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ.",
        "سُبْحَانَ اللَّهِ وَبِحَمْدِهِ، سُبْحَانَ اللَّهِ الْعَظِيمِ.",
        "الْحَمْدُ لِلَّهِ الَّذِي بِنِعْمَتِهِ تَتِمُّ الصَّالِحَاتُ.",
        "لَا حَوْلَ وَلَا قُوَّةَ إِلَّا بِاللَّهِ الْعَلِيِّ الْعَظِيمِ.",
        "أَسْتَغْفِرُ اللَّهَ الْعَظِيمَ لِي وَلِوَالِدَيَّ وَلِلْمُؤْمِنِينَ.",
        "رَضِيَ اللَّهُ عَنِ الصَّحَابَةِ أَجْمَعِينَ."
    ]
    texts.extend(general * 5)
    return texts[:TARGET_COUNT]


def sanitize(text):
    text = text.replace("ﷺ", "صلى الله عليه وسلم")
    # إزالة علامات الوقف (اختياري، لكنه قد يزيل بعض التجويد)
    text = re.sub(r'[\u06D6-\u06ED]', ' ', text)
    # الحفاظ على الحروف العربية والتشكيل وعلامات الترقيم الأساسية
    text = re.sub(r'[^\u0621-\u064A\u064B-\u0652\u0670\s،؟!.]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def convert_to_rvc_wav(wav_path):
    """RVC يحتاج WAV 22050Hz mono. SILMA ينتج 24kHz، فنعيد المعايرة."""
    out = wav_path.with_name(wav_path.stem + "_22k.wav")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(wav_path),
        "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", str(out)
    ], check=True, capture_output=True)
    wav_path.unlink(missing_ok=True)
    out.rename(wav_path)


def main():
    try:
        import silma_tts
    except ImportError:
        print("📦 تثبيت SILMA TTS...")
        subprocess.run([sys.executable, "-m", "pip", "install", "silma-tts"], check=True)

    from silma_tts.api import SilmaTTS

    # الحصول على مسار العينة المرجعية المدمجة (حل مشكلة None)
    try:
        ref_path = get_ref_audio_path()
        print(f"🎤 العينة المرجعية: {ref_path}")
    except Exception as e:
        print(f"❌ خطأ في العينة المرجعية: {e}")
        sys.exit(1)

    print("⏳ تحميل نموذج SILMA TTS...")
    tts = SilmaTTS()

    texts = load_vocalized_texts()
    print(f"✅ {len(texts)} نص جاهز.")

    success = 0
    for idx, text in enumerate(texts, 1):
        clean = sanitize(text)
        if len(clean) < 15:
            continue

        wav_path = OUTPUT_DIR / f"sample_{idx:04d}.wav"

        if wav_path.exists() and wav_path.stat().st_size > 5000:
            success += 1
            continue

        try:
            print(f"[{idx}/{len(texts)}] {clean[:30]}...")
            tts.infer(
                ref_file=ref_path,
                ref_text=REF_TEXT,
                gen_text=clean,
                file_wave=str(wav_path),
                force_tashkeel=False,
                seed=None,
                speed=1.0,
            )
            if wav_path.exists() and wav_path.stat().st_size > 2000:
                convert_to_rvc_wav(wav_path)
                success += 1
            else:
                print("   ⚠️ ملف صغير جداً.")
        except Exception as e:
            print(f"   ❌ فشل: {str(e)[:120]}")

        time.sleep(0.5)

    print("\n" + "=" * 50)
    print(f"✅ اكتمل: {success}/{len(texts)} عينة")
    print(f"📁 المجلد: {OUTPUT_DIR}")
    print("=" * 50)


if __name__ == "__main__":
    main()
