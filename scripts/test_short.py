import os
import re
import sys
import subprocess
import requests
import asyncio
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
import edge_tts

OUT_DIR = Path("out/test_short")
OUT_DIR.mkdir(parents=True, exist_ok=True)

NARRATION_TEXT = "فِي قَدِيمِ الزَّمَانِ، عَاشَ نَبِيٌّ كَرِيمٌ فِي قَوْمٍ ظَالِمِينَ. فَصَبَرَ عَلَى أَذَاهُمْ، وَدَعَا اللَّهَ بِقَلْبٍ خَاشِعٍ."
REF_TEXT = "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ"

# دوال الخطوط والنصوص (مستقلة تماماً)
def _find_arabic_font():
    candidates = [
        "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-BoldItalic.ttf"
    ]
    for c in candidates:
        if os.path.exists(c): return c
    r = subprocess.run(["fc-list", ":lang=ar", "--format=%{file}\n"], capture_output=True, text=True)
    files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
    if files: return files[0]
    raise RuntimeError("لا يوجد خط عربي")

def _shape(text):
    return arabic_reshaper.reshape(str(text))

def _wrap(text, font, max_w, draw):
    words = text.split()
    lines, current = [], ""
    for w in words:
        trial = (current + " " + w).strip() if current else w
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_w: current = trial
        else:
            if current: lines.append(current)
            current = w
    if current: lines.append(current)
    return lines

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())

print("🎙️ إنشاء المرجع الصوتي الآلي...")
async def gen_ref():
    comm = edge_tts.Communicate(REF_TEXT, "ar-SA-HamedNeural")
    await comm.save(str(OUT_DIR / "ref.mp3"))
    subprocess.run(["ffmpeg", "-y", "-i", str(OUT_DIR / "ref.mp3"), "-ar", "16000", "-ac", "1", str(OUT_DIR / "ref.wav")], check=True, capture_output=True)
asyncio.run(gen_ref())
REF_WAV = OUT_DIR / "ref.wav"

def test_audar():
    print("🔊 اختبار Audar...")
    try:
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama
        from neucodec import NeuCodec
        import torch
        import soundfile as sf
        import torchaudio.functional as F

        gguf_path = hf_hub_download(repo_id="audarai/Audar-TTS-V1-Flash", filename="Audar-TTS-V1-Flash-Q4_K_M.gguf", local_dir="./models")
        llm = Llama(model_path=gguf_path, n_ctx=4096, n_gpu_layers=0, verbose=False)
        codec = NeuCodec.from_pretrained("neuphonic/neucodec").eval()

        data, sr = sf.read(str(REF_WAV), dtype="float32")
        if data.ndim > 1: data = data.mean(axis=1)
        if sr != 16000:
            wav = torch.from_numpy(data).unsqueeze(0)
            wav = F.resample(wav, orig_freq=sr, new_freq=16000)
            data = wav.squeeze(0).numpy()
        ref_tensor = torch.from_numpy(data).float().unsqueeze(0).unsqueeze(0)
        ref_codes = codec.encode_code(ref_tensor).squeeze().tolist()
        ref = "".join(f"<|speech_{c}|>" for c in ref_codes)

        prompt = f"user: Convert the text to speech:<|REF_TEXT_START|>{REF_TEXT}<|REF_TEXT_END|><|REF_SPEECH_START|>{ref}<|REF_SPEECH_END|><|TARGET_TEXT_START|>{NARRATION_TEXT}<|TARGET_TEXT_END|>\nassistant:<|TARGET_CODES_START|>"
        tce = llm.tokenize(b"<|TARGET_CODES_END|>", add_bos=False, special=True)[0]
        toks = llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
        
        ids = []
        for tid in llm.generate(toks, temp=0.7, top_k=20, top_p=0.8, repeat_penalty=1.1):
            if tid == tce or len(ids) >= 4096: break
            ids.append(tid)

        text = "".join(llm.detokenize([t], special=True).decode("utf-8", "ignore") for t in ids)
        codes = [int(x) for x in re.findall(r"<\|speech_(\d+)\|>", text)]
        if not codes: raise Exception("فشل التوليد")
        
        wav = codec.decode_code(torch.tensor(codes)[None, None, :]).cpu().numpy()[0, 0, :]
        sf.write(str(OUT_DIR / "audar.wav"), wav, 24000, subtype="PCM_16")
        print("✅ نجح Audar!")
        return True
    except Exception as e:
        print(f"❌ فشل Audar: {str(e)[:100]}")
        return False

def build_video(audio_path):
    print("🎬 بناء الفيديو...")
    duration = get_dur(audio_path)
    bg_path = OUT_DIR / "bg.jpg"
    Image.new("RGB", (1080, 1920), (20, 20, 20)).save(bg_path)
    
    words = NARRATION_TEXT.split()
    chunks = [" ".join(words[i:i+4]) for i in range(0, len(words), 4)]
    time_per_chunk = duration / len(chunks)
    
    text_inputs = []
    text_filters = []
    font_path = _find_arabic_font()
    
    for idx, chunk in enumerate(chunks):
        overlay = Image.new("RGBA", (1080, 1920), (0,0,0,0))
        draw = ImageDraw.Draw(overlay)
        font = ImageFont.truetype(font_path, 60, layout_engine=ImageFont.Layout.RAQM)
        shaped = _shape(chunk)
        bbox = draw.textbbox((0,0), shaped, font=font)
        x = (1080 - (bbox[2]-bbox[0])) // 2
        y = 960 - ((bbox[3]-bbox[1]) // 2)
        draw.text((x, y), shaped, font=font, fill="white", stroke_width=4, stroke_fill="black")
        
        png = OUT_DIR / f"txt_{idx}.png"
        overlay.save(png)
        text_inputs.extend(["-loop", "1", "-i", str(png)])
        text_filters.append(f"[{idx}:v]trim=start=0:end={time_per_chunk},setpts=PTS-STARTPTS,format=rgba[t{idx}]")
    
    concat_str = "".join(f"[t{i}]" for i in range(len(chunks)))
    text_filters.append(f"{concat_str}concat=n={len(chunks)}:v=1:a=0[text]")
    
    text_video = OUT_DIR / "text.mov"
    subprocess.run(["ffmpeg", "-y"] + text_inputs + ["-filter_complex", ";".join(text_filters), "-map", "[text]", "-c:v", "qtrle", "-pix_fmt", "rgba", str(text_video)], check=True, capture_output=True)
    
    final_video = OUT_DIR / "test.mp4"
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(bg_path), "-i", str(text_video), "-i", str(audio_path), "-filter_complex", "[0:v]scale=1080:1920[bg];[bg][1:v]overlay[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(final_video)], check=True, capture_output=True)
    print(f"✅ الفيديو جاهز: {final_video}")

if __name__ == "__main__":
    if test_audar():
        final_audio = OUT_DIR / "audar.wav"
    else:
        print("⚠️ استخدام Edge TTS كخطة بديلة...")
        asyncio.run(edge_tts.Communicate(NARRATION_TEXT, "ar-SA-HamedNeural").save(str(OUT_DIR / "fallback.mp3")))
        subprocess.run(["ffmpeg", "-y", "-i", str(OUT_DIR / "fallback.mp3"), "-ar", "24000", "-ac", "1", str(OUT_DIR / "fallback.wav")], check=True, capture_output=True)
        final_audio = OUT_DIR / "fallback.wav"
    build_video(final_audio)
