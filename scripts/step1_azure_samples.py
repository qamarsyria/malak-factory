"""توليد عينات صوتية بـ Azure TTS (بديل مؤقت لجيميني) لتدريب RVC.
يستخدم النصوص المشكولة مسبقاً لضمان نطق سليم.
"""
import os, sys, gzip, json, subprocess, time, re, wave
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

AZURE_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus")
VOICE = "ar-SA-HamedNeural" # صوت سعودي رجالي فخم ومناسب للقرآن

OUTPUT_DIR = _REPO_ROOT / "rvc_training" / "gemini_samples"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LIBRARIES = _REPO_ROOT / "data" / "libraries"

TARGET_COUNT = 100

def azure_tts(text, out_mp3):
    """توليد الصوت باستخدام Azure."""
    if not AZURE_KEY:
        raise RuntimeError("❌ AZURE_SPEECH_KEY غير موجود!")

    # تنظيف النص من أي رموز قد تسبب مشاكل
    text = text.replace("ﷺ", "صلى الله عليه وسلم")
    text = re.sub(r'[^\u0621-\u064A\u064B-\u0652\u0670\s،؟۔!.]', '', text)
    
    url = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    ssml = f"""<speak version='1.0' xml:lang='ar-SA'>
        <voice name='{VOICE}'>
            <prosody rate='-10%'>{text}</prosody>
        </voice>
    </speak>"""

    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
    }

    r = requests.post(url, headers=headers, data=ssml.encode("utf-8"), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Azure HTTP {r.status_code}: {r.text[:100]}")
    
    Path(out_mp3).write_bytes(r.content)
    return True

def load_vocalized_texts():
    """يجلب نصوصاً مشكولة من المكتبات المحلية."""
    texts = []

    # 1. آيات قرآنية (مشكولة 100%)
    p = LIBRARIES / "ayah-library.json.gz"
    if p.exists():
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                data = json.load(f)
                for item in data[:40]: # أول 40 آية
                    t = item.get("text", "").strip()
                    if 30 < len(t) < 250:
                        texts.append(t)
        except Exception as e: print(f"⚠️ آيات: {e}")

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
        except Exception as e: print(f"⚠️ أحاديث: {e}")

    # 3. نصوص عامة (مشكولة يدوياً لضمان النطق)
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

def convert_to_rvc_wav(mp3_path, wav_path):
    """RVC يحتاج WAV 22050Hz mono."""
    subprocess.run([
        "ffmpeg", "-y", "-i", str(mp3_path),
        "-ar", "22050", "-ac", "1", "-sample_fmt", "s16",
        str(wav_path)
    ], check=True, capture_output=True, timeout=60)

def main():
    if not AZURE_KEY:
        print("❌ تأكد من إضافة AZURE_SPEECH_KEY في GitHub Secrets")
        sys.exit(1)

    print("📖 تحميل نصوص مشكولة...")
    texts = load_vocalized_texts()
    print(f"✅ {len(texts)} نص جاهز.")

    success = 0
    for idx, text in enumerate(texts, 1):
        wav_path = OUTPUT_DIR / f"sample_{idx:04d}.wav"
        mp3_path = OUTPUT_DIR / f"sample_{idx:04d}.mp3"

        if wav_path.exists() and wav_path.stat().st_size > 5000:
            success += 1
            continue

        try:
            print(f"[{idx}/{len(texts)}] {text[:40]}...")
            azure_tts(text, str(mp3_path))
            
            if mp3_path.exists() and mp3_path.stat().st_size > 2000:
                convert_to_rvc_wav(str(mp3_path), str(wav_path))
                mp3_path.unlink()
                success += 1
            else:
                print("   ⚠️ ملف صغير جداً.")
        except Exception as e:
            print(f"   ❌ فشل: {str(e)[:80]}")

        time.sleep(1) # حماية بسيطة

    print("\n" + "="*50)
    print(f"✅ اكتمل: {success}/{len(texts)} عينة")
    print(f"📁 المجلد: {OUTPUT_DIR}")
    print("="*50)

if __name__ == "__main__":
    main()
