"""الخطوة 1: توليد 15-20 دقيقة من صوت Gemini Charon لتدريب RVC.
يستخدم مكتبات المشروع الموجودة (hadith, adhkar) لتنويع النطق.
"""
import os, sys, gzip, json, subprocess, time
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import gemini_tts_with_pool

OUTPUT_DIR = _REPO_ROOT / "rvc_training" / "gemini_samples"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LIBRARIES = _REPO_ROOT / "data" / "libraries"

TARGET_COUNT = 200   # عدد المقاطع (كل مقطع ~5-8 ثواني = ~15-20 دقيقة)
VOICE = "Charon"

def load_samples():
    """يجمع نصوصاً متنوعة من المكتبات المحلية."""
    texts = []

    # 1. أحاديث
    try:
        p = LIBRARIES / "hadith-library.json.gz"
        if p.exists():
            with gzip.open(p, "rt", encoding="utf-8") as f:
                data = json.load(f)
                for item in data[:80]:
                    t = item.get("text", "").strip()
                    if 50 < len(t) < 400:
                        texts.append(t)
    except Exception as e:
        print(f"⚠️ فشل تحميل الأحاديث: {e}")

    # 2. أذكار
    try:
        p = LIBRARIES / "adhkar-library.json.gz"
        if p.exists():
            with gzip.open(p, "rt", encoding="utf-8") as f:
                data = json.load(f)
                for item in data[:60]:
                    t = item.get("text", "").strip()
                    if 30 < len(t) < 300:
                        texts.append(t)
    except Exception as e:
        print(f"⚠️ فشل تحميل الأذكار: {e}")

    # 3. نصوص عامة متنوعة (لتغطية كل الأصوات العربية)
    general = [
        "السلام عليكم ورحمة الله وبركاته، أهلاً وسهلاً بكم في قناتنا.",
        "في هذا اليوم الجميل، نتحدث عن أهمية العلم والتعلم.",
        "الصبر مفتاح الفرج، والتوكل على الله سبيل النجاة.",
        "اللهم ارزقنا العلم النافع والعمل الصالح والرزق الطيب.",
        "كان النبي صلى الله عليه وسلم أحسن الناس خلقاً وأجودهم.",
        "المؤمن القوي خير وأحب إلى الله من المؤمن الضعيف.",
        "من سلك طريقاً يطلب فيه علماً سلك الله به طريقاً إلى الجنة.",
        "قال تعالى: إن الله مع الصابرين الذين إذا أصابتهم مصيبة.",
        "أيها الإخوة، اعلموا أن الدنيا دار ممر والآخرة دار مقر.",
        "رحمة الله واسعة، وتوبته تقبل من عباده التائبين.",
        "جاء رجل إلى النبي فقال: يا رسول الله، أوصني ولا تُطل.",
        "الدنيا ساعة فاجعلها طاعة، والنفس طيعة فاجعلها مطيعة.",
        "إن العبد إذا دعا ربه بقلب خاشع استجاب له.",
        "تقبل الله منا ومنكم صالح الأعمال، وكل عام وأنتم بخير.",
        "سبحان الله وبحمده، سبحان الله العظيم.",
        "الحمد لله الذي بنعمته تتم الصالحات.",
        "لا حول ولا قوة إلا بالله العلي العظيم.",
        "اللهم صل على محمد وعلى آل محمد.",
        "أستغفر الله العظيم لي ولوالدي وللمؤمنين والمؤمنات.",
        "رضي الله عن الصحابة أجمعين وعن التابعين بإحسان.",
    ]
    texts.extend(general * 3)  # تكرار لتغطية صوتية أطول

    return texts[:TARGET_COUNT]

def sanitize(text):
    import re
    text = text.replace("ﷺ", "صلى الله عليه وسلم")
    text = re.sub(r'[\u06D6-\u06ED]', ' ', text)
    text = re.sub(r'[^\u0621-\u064A\u064B-\u0652\u0670\s،؟۔!.]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def convert_to_rvc_wav(mp3_path, wav_path):
    """RVC يحتاج WAV 44100Hz أو 22050Hz mono."""
    subprocess.run([
        "ffmpeg", "-y", "-i", mp3_path,
        "-ar", "22050", "-ac", "1", "-sample_fmt", "s16",
        wav_path
    ], check=True, capture_output=True, timeout=60)

def main():
    print("📖 تحميل نصوص متنوعة...")
    texts = load_samples()
    print(f"✅ {len(texts)} نص جاهز للتوليد")

    success = 0
    for idx, text in enumerate(texts, 1):
        clean = sanitize(text)
        if len(clean) < 20:
            continue

        mp3_path = OUTPUT_DIR / f"sample_{idx:04d}.mp3"
        wav_path = OUTPUT_DIR / f"sample_{idx:04d}.wav"

        if wav_path.exists() and wav_path.stat().st_size > 5000:
            success += 1
            continue

        try:
            print(f"[{idx}/{len(texts)}] {clean[:50]}...")
            gemini_tts_with_pool(clean, str(mp3_path), voice=VOICE)

            if mp3_path.exists() and mp3_path.stat().st_size > 3000:
                convert_to_rvc_wav(str(mp3_path), str(wav_path))
                mp3_path.unlink()  # نحتفظ بالـ WAV فقط
                success += 1
            else:
                print(f"   ⚠️ فشل التوليد")
        except Exception as e:
            print(f"   ❌ فشل: {str(e)[:100]}")

        time.sleep(2)  # rate limit

    # حساب المدة الإجمالية
    total_dur = 0
    for wav in OUTPUT_DIR.glob("*.wav"):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(wav)],
            capture_output=True, text=True
        )
        try:
            total_dur += float(r.stdout.strip())
        except:
            pass

    print("\n" + "="*50)
    print(f"✅ اكتمل: {success}/{len(texts)} عينة")
    print(f"⏱️  المدة الإجمالية: {total_dur/60:.1f} دقيقة")
    print(f"📁 المجلد: {OUTPUT_DIR}")
    print("="*50)

if __name__ == "__main__":
    main()
