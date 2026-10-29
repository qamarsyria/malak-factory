#!/usr/bin/env bash
# test_voxtral.sh — v3 (Python API بدل huggingface-cli)
set -euo pipefail

echo "════════════════════════════════════════════════════════════"
echo "🧪 اختبار Voxtral-4B-TTS-2603 (v3)"
echo "════════════════════════════════════════════════════════════"

WORK_DIR="out/test_voxtral"
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

# ═══ 1. تنزيل الـ zip ═══
if [ ! -d "voxtral-tts-linux-x86_64" ]; then
    echo "⬇️ [1/6] تنزيل voxtral_tts_rs..."
    curl -LO https://github.com/second-state/voxtral_tts_rs/releases/latest/download/voxtral-tts-linux-x86_64.zip
    unzip -oq voxtral-tts-linux-x86_64.zip
else
    echo "⏭️ [1/6] المجلد موجود"
fi

# ═══ 2. الدخول للمجلد ═══
cd voxtral-tts-linux-x86_64
chmod +x voxtral-tts 2>/dev/null || true

# ═══ 3. تنزيل النموذج (Python API — لا يحتاج CLI) ═══
echo ""
echo "⬇️ [3/6] تنزيل النموذج عبر Python API..."
pip install -q "huggingface_hub>=0.26"

python3 << 'PYEOF'
import os
import sys
from huggingface_hub import snapshot_download

token = os.environ.get("HF_TOKEN", "").strip()
if token:
    print(f"🔑 Token موجود (طوله: {len(token)} حرف)")
else:
    print("⚠️ لا توكن — قد يفشل إذا النموذج gated")

try:
    path = snapshot_download(
        repo_id="mistralai/Voxtral-4B-TTS-2603",
        local_dir="models/voxtral-4b-tts",
        token=token if token else None,
    )
    print(f"✅ النموذج: {path}")
except Exception as e:
    print(f"❌ فشل: {type(e).__name__}: {e}")
    sys.exit(1)
PYEOF

# ═══ 4. نسخ voice embeddings ═══
echo ""
echo "📋 [4/6] نسخ voice embeddings..."
mkdir -p models/voxtral-4b-tts/voice_embedding
cp voice_embedding/*.safetensors models/voxtral-4b-tts/voice_embedding/ 2>/dev/null || true
echo "   ملف ar_male:"
ls -la models/voxtral-4b-tts/voice_embedding/ar_male.safetensors 2>/dev/null || echo "   ⚠️ غير موجود"

# ═══ 5. عرض الأصوات (تشخيص) ═══
echo ""
echo "🎤 [5/6] الأصوات المتاحة (قبل التوليد):"
./voxtral-tts models/voxtral-4b-tts --list-voices 2>&1 | head -50 || echo "⚠️ list-voices فشل"

# ═══ 6. التوليد ═══
echo ""
echo "🎙️ [6/6] توليد: بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ"
echo "    الصوت: ar_male (ذكر)"

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
    SIZE=$(stat -c%s fatiha_1.wav)
    DUR=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 fatiha_1.wav 2>/dev/null || echo "?")
    echo "════════════════════════════════════════════════════════════"
    echo "✅ نجح! fatiha_1.wav"
    echo "   الحجم: ${SIZE} bytes | المدة: ${DUR}s"
    echo "════════════════════════════════════════════════════════════"
else
    echo "════════════════════════════════════════════════════════════"
    echo "❌ لم يُنتج الملف"
    ls -la . 2>/dev/null || true
    exit 1
fi
