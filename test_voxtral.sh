#!/usr/bin/env bash
# test_voxtral.sh — اختبار Voxtral-4B-TTS على آية واحدة (مُصحَّح v2)
set -euo pipefail

echo "════════════════════════════════════════════════════════════"
echo "🧪 اختبار Voxtral-4B-TTS-2603 (آية الفاتحة:1)"
echo "════════════════════════════════════════════════════════════"

WORK_DIR="out/test_voxtral"
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

# ═══ 1. تنزيل الـ zip إن لم يكن موجوداً ═══
if [ ! -d "voxtral-tts-linux-x86_64" ]; then
    echo ""
    echo "⬇️ [1/5] تنزيل voxtral_tts_rs (Linux CPU)..."
    curl -LO https://github.com/second-state/voxtral_tts_rs/releases/latest/download/voxtral-tts-linux-x86_64.zip
    unzip -oq voxtral-tts-linux-x86_64.zip
else
    echo "⏭️ [1/5] المجلد موجود مسبقاً"
fi

# ═══ 2. الدخول للمجلد وجعل الملفات قابلة للتنفيذ ═══
echo ""
echo "🔧 [2/5] تجهيز الملفات التنفيذية..."
cd voxtral-tts-linux-x86_64
chmod +x voxtral-tts voxtral-tts-server 2>/dev/null || true

echo "📂 محتوى المجلد:"
ls -la | head -20

echo ""
echo "🆘 خيارات voxtral-tts (للتشخيص):"
./voxtral-tts --help 2>&1 | head -30 || echo "⚠️ --help فشل"

# ═══ 3. تنزيل النموذج ═══
echo ""
echo "⬇️ [3/5] تنزيل النموذج (8GB — قد يأخذ 10-15 دقيقة)..."

# أولاً: هل يوجد سكربت جاهز في الـ zip؟
if [ -f "scripts/download_model.sh" ]; then
    echo "   → استخدام scripts/download_model.sh"
    bash scripts/download_model.sh
else
    echo "   → لا يوجد سكربت — استخدام huggingface-cli"
    pip install -q huggingface-hub 2>/dev/null || pip install -q --user huggingface-hub
    
    # قد يحتاج توكن إذا كان النموذج gated
    HF_ARGS=""
    if [ -n "${HF_TOKEN:-}" ]; then
        HF_ARGS="--token ${HF_TOKEN}"
        echo "   → استخدام HF_TOKEN"
    fi
    
    huggingface-cli download mistralai/Voxtral-4B-TTS-2603 \
        --local-dir models/voxtral-4b-tts \
        ${HF_ARGS} 2>&1 | tail -20 \
        || {
            echo "❌ فشل تنزيل النموذج"
            echo "   قد يكون النموذج gated — يحتاج قبول الترخيص على HF"
            echo "   أو انتهت مساحة القرص (النموذج 8GB، المتاح ~14GB)"
            exit 1
        }
fi

echo "✅ النموذج جاهز:"
du -sh models/voxtral-4b-tts 2>/dev/null || echo "⚠️ المجلد غير موجود"

# ═══ 4. نسخ voice embeddings ═══
echo ""
echo "📋 [4/5] نسخ voice embeddings..."
mkdir -p models/voxtral-4b-tts/voice_embedding
cp voice_embedding/*.safetensors models/voxtral-4b-tts/voice_embedding/ 2>/dev/null || true
echo "   ملفات ar_male:"
ls -la models/voxtral-4b-tts/voice_embedding/ar_male.safetensors 2>/dev/null || echo "   ⚠️ ar_male غير موجود"

# ═══ 5. التوليد ═══
echo ""
echo "🎙️ [5/5] توليد: بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ (ar_male)"

./voxtral-tts models/voxtral-4b-tts \
    --text "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ" \
    --voice ar_male \
    --output ../fatiha_1.wav \
    --max-tokens 4096 \
    2>&1 | tail -30

# ═══ التحقق ═══
echo ""
cd ..
if [ -f "fatiha_1.wav" ]; then
    SIZE=$(stat -c%s fatiha_1.wav 2>/dev/null || stat -f%z fatiha_1.wav)
    DUR=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 fatiha_1.wav 2>/dev/null || echo "?")
    echo "════════════════════════════════════════════════════════════"
    echo "✅ نجح! الملف: fatiha_1.wav"
    echo "   الحجم: ${SIZE} bytes"
    echo "   المدة: ${DUR}s"
    echo "════════════════════════════════════════════════════════════"
else
    echo "════════════════════════════════════════════════════════════"
    echo "❌ لم يُنتج الملف — راجع السجل أعلاه"
    echo "════════════════════════════════════════════════════════════"
    ls -la . 2>/dev/null || true
    exit 1
fi
