"""اختبار شامل: edge-tts + تعديلات النبرة والسرعة."""
import asyncio
import subprocess
from pathlib import Path
import edge_tts

# إصلاح خادم edge-tts
edge_tts.communicate.WSS_URL = (
    "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
)

# جملة اختبار (سرد قصصي نموذجي)
TEST_TEXT = (
    "يقول الله تعالى مبيناً عظمة كتابه، حيث تبدأ السورة بالحروف المقطعة. "
    "وفي هذه اللحظة، تتجلى آيات هذا الكتاب الحكيم لتعلن عن بيانها المطلق."
)
TEST_VOICE = "ar-EG-ShakirNeural"
SAMPLE_RATE = 24000


def _get_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except:
        return 0.0


async def _tts(text, out_path, voice, rate):
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(out_path)


def _apply_filters(input_path, output_path, pitch_factor=1.0, tempo=1.0):
    """
    pitch_factor: 0.85 = نبرة أعمق 15% | 1.0 = بدون تغيير
    tempo: 0.85 = أبطأ 15% | 1.0 = بدون تغيير
    """
    filters = []

    # 1) خفض النبرة (asetrate يخفض النبرة والسرعة)
    if pitch_factor != 1.0:
        new_rate = int(SAMPLE_RATE * pitch_factor)
        filters.append(f"asetrate={new_rate}")
        # نعوّض خفض السرعة الناتج عن asetrate بـ atempo
        filters.append(f"atempo={1/pitch_factor:.4f}")

    # 2) تبطيء إضافي
    if tempo != 1.0:
        filters.append(f"atempo={tempo}")

    # 3) إعادة المعايرة النهائية
    filters.append(f"aresample={SAMPLE_RATE}")

    filter_chain = ",".join(filters)

    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-filter:a", filter_chain,
         "-codec:a", "libmp3lame", "-q:a", "4",
         output_path],
        check=True, capture_output=True, timeout=120,
    )


def test_variant(name, rate="-10%", pitch=1.0, tempo=1.0):
    """يولّد نسخة واحدة بتعديلات محددة."""
    Path("out").mkdir(exist_ok=True)
    raw = f"out/test-{name}-raw.mp3"
    final = f"out/test-{name}.mp3"

    print(f"\n{'='*60}")
    print(f"🎵 {name}")
    print(f"   rate={rate}, pitch_factor={pitch}, tempo={tempo}")
    print(f"{'='*60}")

    # 1) edge-tts
    asyncio.run(_tts(TEST_TEXT, raw, TEST_VOICE, rate))
    raw_dur = _get_duration(raw)
    print(f"   المدة الخام: {raw_dur:.2f} ثانية")

    # 2) فلاتر
    _apply_filters(raw, final, pitch_factor=pitch, tempo=tempo)
    final_dur = _get_duration(final)
    print(f"   المدة النهائية: {final_dur:.2f} ثانية")
    if raw_dur > 0:
        print(f"   نسبة التغيير: {final_dur/raw_dur:.2f}x")

    return final


def main():
    print("=" * 60)
    print("🧪 اختبار شامل: النبرة + السرعة")
    print("=" * 60)
    print(f"النص: {TEST_TEXT[:80]}...")
    print(f"الصوت: {TEST_VOICE}")

    # 8 نسخ للتجربة
    variants = [
        # (الاسم،       rate,   pitch, tempo)
        ("01-raw",          "-10%", 1.00, 1.00),  # خام (مرجع)
        ("02-rate",         "-25%", 1.00, 1.00),  # rate فقط
        ("03-tempo",        "-10%", 1.00, 0.75),  # tempo فقط
        ("04-pitch",        "-10%", 0.85, 1.00),  # خفض نبرة فقط
        ("05-pitch-tempo",  "-10%", 0.85, 0.85),  # نبرة + سرعة
        ("06-pitch-deep",   "-10%", 0.80, 0.80),  # نبرة أعمق
        ("07-pitch-tempo-deep","-10%",0.75, 0.75),# أعمق وأبطأ
        ("08-combo",        "-20%", 0.85, 0.80),  # تركيبة
    ]

    for name, rate, pitch, tempo in variants:
        try:
            test_variant(name, rate=rate, pitch=pitch, tempo=tempo)
        except Exception as e:
            print(f"❌ فشل {name}: {e}")

    print(f"\n{'='*60}")
    print("✅ تم إنشاء 8 نسخ")
    print("   الملفات في: out/test-*.mp3")
    print("   اسمعها، واختر الأفضل.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
