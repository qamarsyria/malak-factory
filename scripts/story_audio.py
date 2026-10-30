import os
import sys
import json
import subprocess
import tempfile
import shutil
import re
import time
import requests
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir, notify_telegram
from gemini_tts_pool import gemini_tts_with_pool

TARGET_SR = "24000"
SILENCE_DUR = 0.0 # إزالة الصمت تماماً للحفاظ على التزامن

# النص المرجعي الشامل (تدريب Audar)
REF_TEXT = (
    "اللَّهُ لَا إِلَهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ يُحْيِي وَيُمِيتُ وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ. "
    "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ. نَحْنُ نَقُصُّ عَلَيْكَ أَحْسَنَ الْقَصَصِ إِيمَانًا. "
    "صَدَقَ اللَّهُ الْعَظِيمُ. ذَهَبْتُ إِلَى مَدِينَةِ فُسْطَاطَ وَمَدِينَةِ قَلْقِيلِيَةَ. "
    "وَالْأَرْضَ مَدَدْنَاهَا وَأَلْقَيْنَا فِيهَا رَوَاسِيَ. وَلَا الضَّالِّينَ. هَذَا فَضْلٌ كَبِيرٌ. وَجَعَلْنَا فِيهَا جَنَّاتٍ خَضْرَاءَ."
)

def clean(t):
    t = re.sub(r'\[.*?\]|\(.*?\)|﴿.*?﴾', '', t)
    t = re.sub(r'[a-zA-Z]', '', t)
    return re.sub(r"\s+", " ", re.sub(r"[ۖۗۘۙۚۛ۞]", "", t.replace("۝", " "))).strip()

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try: return round(float(r.stdout.strip()), 3)
    except: return 0.0

def fetch_ayah(s, a):
    try:
        r = requests.get(f"{QURAN_API}/ayah/{s}:{a}", timeout=10)
        if r.status_code == 200: return r.json()["data"]["text"]
    except: pass
    return ""

def split_text(text, max_chars=150):
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chars:
            current += " " + s
        else:
            if current: chunks.append(current.strip())
            current = s
    if current: chunks.append(current.strip())
    return chunks

def generate_audar(text, ref_wav, out_wav):
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
        
        data, sr = sf.read(str(ref_wav), dtype="float32")
        if data.ndim > 1: data = data.mean(axis=1)
        if sr != 16000:
            wav = torch.from_numpy(data).unsqueeze(0)
            wav = F.resample(wav, orig_freq=sr, new_freq=16000)
            data = wav.squeeze(0).numpy()
        ref_tensor = torch.from_numpy(data).float().unsqueeze(0).unsqueeze(0)
        ref_codes = codec.encode_code(ref_tensor).squeeze().tolist()
        ref = "".join(f"<|speech_{c}|>" for c in ref_codes)
        
        prompt = f"user: Convert the text to speech:<|REF_TEXT_START|>{REF_TEXT}<|REF_TEXT_END|><|REF_SPEECH_START|>{ref}<|REF_SPEECH_END|><|TARGET_TEXT_START|>{text}<|TARGET_TEXT_END|>\nassistant:<|TARGET_CODES_START|>"
        tce = llm.tokenize(b"<|TARGET_CODES_END|>", add_bos=False, special=True)[0]
        toks = llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
        
        ids = []
        for tid in llm.generate(toks, temp=0.7, top_k=20, top_p=0.8, repeat_penalty=1.1):
            if tid == tce or len(ids) >= 4096: break
            ids.append(tid)
            
        raw_text = "".join(llm.detokenize([t], special=True).decode("utf-8", "ignore") for t in ids)
        codes = [int(x) for x in re.findall(r"<\|speech_(\d+)\|>", raw_text)]
        if len(codes) < 50: return False
        
        wav = codec.decode_code(torch.tensor(codes)[None, None, :]).cpu().numpy()[0, 0, :]
        sf.write(str(out_wav), wav, 24000, subtype="PCM_16")
        return True
    except Exception as e:
        print(f"   ⚠️ Audar Error: {str(e)[:80]}")
        return False

def build_episode_audio(json_path, out_mp3):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    
    # 1. مرجع Gemini
    print("🎙️ توليد المرجع (Gemini)...")
    ref_mp3 = work / "ref.mp3"
    try:
        gemini_tts_with_pool(REF_TEXT, str(ref_mp3), voice="Charon")
        ref_wav = work / "ref.wav"
        subprocess.run(["ffmpeg", "-y", "-i", str(ref_mp3), "-ar", "16000", "-ac", "1", str(ref_wav)], check=True, capture_output=True)
    except Exception as e:
        raise RuntimeError(f"فشل توليد المرجع: {e}")

    final_wavs = []
    all_segments_meta = []

    for idx, seg in enumerate(ep.get("segments", [])):
        stype = seg.get("type")
        text, display = "", ""
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = clean(seg["text"])
        elif stype == "quran":
            s, ayahs = seg["surah"], seg["ayah_numbers"]
            text = " ".join(filter(None, (fetch_ayah(s, a) for a in ayahs)))
            display = f"{seg.get('surah_name', '')}: {ayahs[0]}"
        
        if not text: continue
        
        chunks = split_text(text)
        chunk_timings = []
        
        print(f"🎙️ المقطع {idx+1} [{stype}] - {len(chunks)} فقرة...", flush=True)
        
        for c_idx, chunk in enumerate(chunks):
            if not chunk.strip(): continue
            
            raw_wav = work / f"c_{idx}_{c_idx}.wav"
            success = False
            
            if stype == "quran":
                try:
                    temp_mp3 = work / f"temp_{idx}_{c_idx}.mp3"
                    gemini_tts_with_pool(chunk, str(temp_mp3), voice="Charon")
                    if temp_mp3.exists() and temp_mp3.stat().st_size > 3000:
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                        success = True
                except Exception:
                    pass
            
            if not success:
                success = generate_audar(chunk, ref_wav, raw_wav)
            
            if not success:
                print(f"   ❌ فشل تام. تخطي فقرة.", flush=True)
                continue # تخطي بدون وضع صمت

            # إصلاح الخشّة: تلاشي قصير
            faded = work / f"faded_{idx}_{c_idx}.wav"
            c_dur = get_dur(raw_wav)
            if c_dur > 0.2:
                subprocess.run(["ffmpeg", "-y", "-i", str(raw_wav), "-af", "afade=t=in:st=0:d=0.05,afade=t=out:st=" + str(c_dur-0.05) + ":d=0.05", str(faded)], check=True, capture_output=True)
            else:
                shutil.copy(raw_wav, faded)

            final_wavs.append(faded)
            chunk_timings.append({"text": chunk, "duration": get_dur(faded)})
            time.sleep(1)
            
        if chunk_timings:
            all_segments_meta.append({
                "index": idx, "type": stype, "display": display,
                "chunks": chunk_timings
            })

    if not final_wavs: raise RuntimeError("لا يوجد صوت")

    # الدمج النهائي
    final_concat = work / "final.txt"
    final_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in final_wavs), encoding="utf-8")
    
    merged_wav = work / "merged.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(final_concat), "-c", "copy", str(merged_wav)], check=True, capture_output=True)
    
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(merged_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)], check=True, capture_output=True)
    
    meta_path = str(out_mp3).replace(".mp3", "_meta.json")
    Path(meta_path).write_text(json.dumps({
        "episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "story_id": ep.get("story_id", ""), 
        "total_duration": get_dur(out_mp3), "segments": all_segments_meta
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {out_mp3} | {get_dur(out_mp3)/60:.1f} دقيقة", flush=True)
    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_episode_audio(sys.argv[1], sys.argv[2])
