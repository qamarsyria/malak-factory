import os, sys, gzip, json, subprocess, time, re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from gemini_tts_pool import gemini_tts_with_pool
except ImportError:
    print("❌ تأكد من وجود gemini_tts_pool.py في مجلد scripts")
    sys.exit(1)

OUTPUT_DIR = _REPO_ROOT / "rvc_training" / "gemini_samples"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LIBRARIES = _REPO_ROOT / "data" / "libraries"

TARGET_COUNT = 100  # 100 مقطع كافية جداً للبداية
VOICE = "Charon"

def load_samples():
    texts = []
    # نصوص عامة متنوعة
    general = [
        "السلام عليكم ورحمة الله وبركاته، أهلاً وسهلاً بكم.",
        "الصبر مفتاح الفرج، والتوكل على الله سبيل النجاة.",
        "اللهم ارزقنا العلم النافع والعمل الصالح.",
        "كان النبي صلى الله عليه وسلم أحسن الناس خلقاً.",
        "من سلك طريقاً يطلب فيه علماً سلك الله به طريقاً إلى الجنة.",
        "قال تعالى: إن الله مع الصابرين.",
        "الدنيا ساعة فاجعلها طاعة.",
        "تقبل الله منا ومنكم صالح الأعمال.",
        "سبحان الله وبحمده، سبحان الله العظيم.",
        "الحمد لله الذي بنعمته تتم الصالحات.",
        "لا حول ولا قوة إلا بالله العلي العظيم.",
        "اللهم صل على محمد وعلى آل محمد.",
        "أستغفر الله العظيم لي ولوالدي.",
        "رضي الله عن الصحابة أجمعين."
    ]
    texts.extend(general * 8) # تكرار لتغطية أطول
    
    # جلب أحاديث إذا وجدت
    p = LIBRARIES / "hadith-library.json.gz"
    if p.exists():
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                data = json.load(f)
                for item in data[:30]:
                    t = item.get("text", "").strip()
                    if 30 < len(t) < 200:
                        texts.append(t)
        except: pass
        
    return texts[:TARGET_COUNT]

def sanitize(text):
    text = text.replace("ﷺ", "صلى الله عليه وسلم")
    text = re.sub(r'[\u06D6-\u06ED]', ' ', text)
    text = re.sub(r'[^\u0621-\u064A\u064B-\u0652\u0670\s،؟۔!.]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def main():
    texts = load_samples()
    print(f"✅ {len(texts)} نص جاهز.")

    for idx, text in enumerate(texts, 1):
        clean = sanitize(text)
        if len(clean) < 15: continue

        wav_path = OUTPUT_DIR / f"sample_{idx:04d}.wav"
        mp3_path = OUTPUT_DIR / f"sample_{idx:04d}.mp3"

        if wav_path.exists() and wav_path.stat().st_size > 5000: continue

        try:
            print(f"[{idx}/{len(texts)}] {clean[:30]}...")
            gemini_tts_with_pool(clean, str(mp3_path), voice=VOICE)

            if mp3_path.exists() and mp3_path.stat().st_size > 2000:
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(mp3_path),
                    "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", str(wav_path)
                ], check=True, capture_output=True)
                mp3_path.unlink()
        except Exception as e:
            print(f"   ⚠️ فشل: {str(e)[:80]}")
            
        time.sleep(3)

    print("✅ اكتمل التوليد.")

if __name__ == "__main__":
    main()
