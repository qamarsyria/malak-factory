#!/usr/bin/env bash
# test_voxtral.sh — اختبار Voxtral-4B-TTS على آية واحدة
# يعمل على Linux CPU (GitHub Actions)
set -euo pipefail

echo "════════════════════════════════════════════════════════════"
echo "🧪 اختبار Voxtral-4B-TTS-2603 (آية الفاتحة:1)"
echo "════════════════════════════════════════════════════════════"

OUT_DIR="out/test_voxtral"
mkdir -p "${OUT_DIR}"
cd "${OUT_DIR}"

# ═══ 1. تنزيل الأداة (Linux CPU) ═══
echo ""
echo "⬇️ [1/4] تنزيل voxtral_tts_rs (Linux CPU)..."
if [ ! -f "voxtral-tts" ]; then
    curl -LO https://github.com/second-state/voxtral_tts_rs/releases/latest/download/voxtral-tts-linux-x86_64.zip
    unzip -o voxtral-tts-linux-x86_64.zip
    chmod +x voxtral-tts
    echo "✅ الأداة جاهزة"
else
    echo "⏭️ الأداة موجودة مسبقاً"
fi

# ═══ 2. تنزيل النموذج (8GB) ═══
echo ""
echo "⬇️ [2/4] تنزيل النموذج (8GB — قد يأخذ 5-10 دقائق)..."
if [ ! -d "models/voxtral-4b-tts" ]; then
    bash <(curl -sSf https://raw.githubusercontent.com/second-state/voxtral_tts_rs/main/scripts/download_model.sh)
    echo "✅ النموذج جاهز"
else
    echo "⏭️ النموذج موجود مسبقاً"
fi

# ═══ 3. نسخ voice embeddings ═══
echo ""
echo "📋 [3/4] نسخ voice embeddings..."
if [ -d "voice_embedding" ] && [ -d "models/voxtral-4b-tts" ]; then
    mkdir -p models/voxtral-4b-tts/voice_embedding
    cp voice_embedding/*.safetensors models/voxtral-4b-tts/voice_embedding/ 2>/dev/null || true
    echo "✅ تم النسخ"
else
    echo "⚠️ مجلد voice_embedding غير موجود — سنحاول المتابعة"
fi

# ═══ 4. توليد الآية ═══
echo ""
echo "🎙️ [4/4] توليد: بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ"
echo "    الصوت: ar_male"

./voxtral-tts models/voxtral-4b-tts \
    --text "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ" \
    --voice ar_male \
    --output fatiha_1.wav \
    --max-tokens 4096 \
    || {
        echo "❌ فشل التوليد"
        exit 1
    }

# ═══ التحقق ═══
echo ""
if [ -f "fatiha_1.wav" ]; then
    SIZE=$(stat -c%s fatiha_1.wav 2>/dev/null || stat -f%z fatiha_1.wav)
    DUR=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 fatiha_1.wav 2>/dev/null || echo "?")
    echo "════════════════════════════════════════════════════════════"
    echo "✅ نجح! الملف: fatiha_1.wav"
    echo "   الحجم: ${SIZE} bytes"
    echo "   المدة: ${DUR}s"
    echo "════════════════════════════════════════════════════════════"
else
    echo "❌ لم يُنتج الملف"
    exit 1
fi
