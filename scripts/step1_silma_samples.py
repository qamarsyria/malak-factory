"""توليد عينات صوتية بـ SILMA TTS (بدون تسجيل، بدون أرقام) لتدريب RVC.
يستخدم النصوص المشكولة مسبقاً من مكتباتك لضمان نطق سليم.
"""
import os, sys, gzip, json, subprocess, time, re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUTPUT_DIR = _REPO_ROOT / "rvc_training" / "silma_samples"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LIBRARIES = _REPO_ROOT / "data" / "libraries"

# يمكنك تغيير الصوت حسب ما تفضّل (مثلاً صوت رجالي)
# SILMA TTS يستخدم عينة مرجعية لاستنساخ الصوت.
# إذا لم تضع عينة، سيستخدم صوتاً افتراضياً.
REF_AUDIO = ""  # اتركه فارغاً للصوت الافتراضي
REF_TEXT = ""

TARGET_COUNT = 100

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

    # 3. نصوص عامة مشكولة يدوياً (لضمان النطق)
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
    text = re.sub(r'[\u06D6-\u06ED]', ' ', text)
    text = re.sub(r'[^\u0621-\u064A\u064B-\u0652\u0670\s،؟۔!.]', '', text)
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
    # تثبيت SILMA TTS إذا لم يكن مثبتاً
    try:
        import silma_tts
    except ImportError:
        print("📦 تثبيت SILMA TTS...")
        subprocess.run([sys.executable, "-m", "pip", "install", "silma-tts"], check=True)

    from silma_tts.api import SilmaTTS
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
                ref_file=REF_AUDIO if REF_AUDIO and Path(REF_AUDIO).exists() else None,
                ref_text=REF_TEXT if REF_TEXT else None,
                gen_text=clean,
                file_wave=str(wav_path),
            )
            if wav_path.exists() and wav_path.stat().st_size > 2000:
                convert_to_rvc_wav(wav_path)
                success += 1
            else:
                print("   ⚠️ ملف صغير جداً.")
        except Exception as e:
            print(f"   ❌ فشل: {str(e)[:80]}")

        time.sleep(0.5)

    print("\n" + "="*50)
    print(f"✅ اكتمل: {success}/{len(texts)} عينة")
    print(f"📁 المجلد: {OUTPUT_DIR}")
    print("="*50)

if __name__ == "__main__":
    main()
