import os
import subprocess
import sys
import math
import json
from faster_whisper import WhisperModel
from pydub import AudioSegment
import numpy as np
import librosa
import warnings
warnings.filterwarnings("ignore")

# الإعدادات
INPUT_AUDIO = sys.argv[1]
OUTPUT_AUDIO = sys.argv[2]
MODEL_PATH = "malak_voice.pth"
INDEX_PATH = "added_malak_voice.index"
RVC_DIR = "Retrieval-based-Voice-Conversion-WebUI"
TEMP_DIR = "tmp_chunks"
PITCH_OFFSET = 0  # لو محتاجين نعدل الحدة يدوياً

os.makedirs(TEMP_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════
# 1. تبطيء الصوت قليلاً قبل المعالجة
# ═══════════════════════════════════════════════════════════
print("⏳ Step 1: Slowing down audio for better alignment...")
slowed_audio = "slowed_input.wav"
subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "16000", "-ac", "1", "-af", "atempo=0.8", slowed_audio], capture_output=True)

# ═══════════════════════════════════════════════════════════
# 2. استخدام Whisper للمحاذاة القسرية
# ═══════════════════════════════════════════════════════════
print("⏳ Step 2: Running Whisper Forced Alignment...")
model_size = "small"  # ممكن نستخدم medium أو large للدقة الأعلى
model = WhisperModel(model_size, device="cpu", compute_type="float32")

# النص القرآني لآية مريم 11
expected_text = "فَخَرَجَ عَلَىٰ قَوْمِهِ مِنَ الْمِحْرَابِ فَأَوْحَىٰ إِلَيْهِمْ أَن سَبِّحُوا بُكْرَةً وَعَشِيًّا"

# هام: نمرر النص عشان نجبر Whisper يطلع توقيتاته بالظبط
segments, _ = model.transcribe(slowed_audio, word_timestamps=True, initial_prompt=expected_text)

# ═══════════════════════════════════════════════════════════
# 3. تقطيع الصوت لكل كلمة
# ═══════════════════════════════════════════════════════════
print("⏳ Step 3: Slicing audio into words...")
audio = AudioSegment.from_wav(slowed_audio)
chunk_files = []

for i, segment in enumerate(segments):
    for j, word in enumerate(segment.words):
        start_ms = int(word.start * 1000)
        end_ms = int(word.end * 1000)
        # إضافة 50 مللي ثانية حماية عشان ما نقص الكلمة
        start_ms = max(0, start_ms - 50)
        end_ms = min(len(audio), end_ms + 50)
        
        chunk = audio[start_ms:end_ms]
        chunk_file = f"{TEMP_DIR}/word_{i:03d}_{j:03d}.wav"
        chunk.export(chunk_file, format="wav")
        chunk_files.append(chunk_file)
        print(f"   Word: {word.word.strip()} | {start_ms}ms - {end_ms}ms")

# ═══════════════════════════════════════════════════════════
# 4. تمرير كل كلمة على RVC
# ═══════════════════════════════════════════════════════════
print("⏳ Step 4: Converting each word via RVC...")
converted_files = []

# تحضير البيئة لـ RVC
env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

for idx, chunk_file in enumerate(chunk_files):
    out_chunk = f"{TEMP_DIR}/conv_{idx:03d}.wav"
    # نستخدم الكود الذهبي هنا
    cmd = [
        sys.executable, "-m", "infer.cli",
        "--model", MODEL_PATH, "--input", chunk_file, "--output", out_chunk,
        "--f0-method", "rmvpe", "--pitch", "12",  # هون بنرفع الحدة من الرجل للبنت
        "--protect", "0.5",
        "--index-rate", "0.8",
        "--rms-mix-rate", "0.25",
        "--index", INDEX_PATH, "--overwrite"
    ]
    res = subprocess.run(cmd, cwd=RVC_DIR, env=env, capture_output=True, text=True)
    if res.returncode == 0 and os.path.exists(out_chunk):
        converted_files.append(out_chunk)
    else:
        print(f"❌ Failed RVC on {chunk_file}: {res.stderr[-200:]}")

# ═══════════════════════════════════════════════════════════
# 5. دمج الكلمات المحولة
# ═══════════════════════════════════════════════════════════
print("⏳ Step 5: Merging converted chunks...")
merged_audio = AudioSegment.empty()
for f in converted_files:
    if os.path.exists(f):
        merged_audio += AudioSegment.from_wav(f)

merged_path = "merged_slowed.wav"
merged_audio.export(merged_path, format="wav")

# ═══════════════════════════════════════════════════════════
# 6. إعادة السرعة الطبيعية + الفلاتر
# ═══════════════════════════════════════════════════════════
print("⏳ Step 6: Restoring speed and applying final polish...")
post_filters = (
    "atempo=1.25," # نرجع السرعة (1 / 0.8)
    "highpass=f=90,"
    "lowpass=f=14000,"
    "afftdn=nf=-30,"
    "equalizer=f=350:t=q:w=2:g=-3,"
    "equalizer=f=1200:t=q:w=2:g=2.5,"
    "equalizer=f=2500:t=q:w=2:g=2,"
    "equalizer=f=4500:t=q:w=2:g=-2,"
    "equalizer=f=8000:t=q:w=3:g=-3,"
    "acompressor=threshold=0.089:ratio=3:attack=20:release=250,"
    "alimiter=level_in=1:level_out=0.9"
)

subprocess.run(["ffmpeg", "-y", "-i", merged_path, "-af", post_filters, "-ar", "48000", "-ac", "1", "-b:a", "192k", OUTPUT_AUDIO], capture_output=True)

print(f"✅ Done! Final file: {OUTPUT_AUDIO}")
