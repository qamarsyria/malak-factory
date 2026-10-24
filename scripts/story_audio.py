import os, subprocess, tempfile, json, sys, random
from pathlib import Path
import requests
from config import QURAN_BASE, SAFE_RECITERS
from voice import generate as gemini_generate
from smart_director import direct_scene
from sfx_manager import fetch_sfx

TARGET_BR = "192k"
SFX_VOLUME = 0.05

def normalize(input_path, output_path):
    subprocess.run(["ffmpeg", "-y", "-i", input_path, "-ar", "44100", "-ac", "1", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_path], check=True, capture_output=True)

def fetch_ayah_audio(reciter, surah, ayah, out_path):
    url = f"{QURAN_BASE}/{reciter}/{surah:03d}{ayah:03d}.mp3"
    r = requests.get(url, timeout=60)
    if r.status_code == 200:
        Path(out_path).write_bytes(r.content)
    else:
        raise RuntimeError(f"فشل جلب الآية {surah}:{ayah} من {reciter} - HTTP {r.status_code}")

def fetch_ayah_text(surah, ayah):
    try:
        r = requests.get(f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/ar.alafasy", timeout=10)
        if r.status_code == 200: return r.json()['data']['text']
    except: pass
    return ""

def get_duration(path):
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True, timeout=10)
    try: return round(float(probe.stdout.strip()), 3)
    except: return 0.0

def add_fade(input_path, output_path):
    dur = get_duration(input_path)
    subprocess.run(["ffmpeg", "-y", "-i", input_path, "-af", f"afade=t=in:st=0:d=0.1,afade=t=out:st={max(0, dur-0.15)}:d=0.15", "-c:a", "libmp3lame", "-b:a", "192k", output_path], check=True, capture_output=True)

def build_episode_audio(episode_json_path, output_mp3):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    parts, meta_segments, idx = [], [], 0
    
    # 🌟 اختيار قارئ عشوائي آمن لكل حلقة
    reciter = random.choice(SAFE_RECITERS)
    print(f"🎙️ القارئ المختار لهذه الحلقة: {reciter}", flush=True)

    for seg in ep.get("segments", []):
        seg_type = seg.get("type")
        raw_path = work_dir / f"raw_{idx:03d}.mp3"
        mixed_path = work_dir / f"mixed_{idx:03d}.mp3"
        fade_path = work_dir / f"fade_{idx:03d}.mp3"
        norm_path = work_dir / f"norm_{idx:03d}.mp3"

        if seg_type in ("narration", "teaser", "outro"):
            direction = {"visual": "mosque", "sfx": "", "voice_style": seg["emotion_hint"]} if seg.get("emotion_hint") else direct_scene(seg["text"])
            gemini_generate(seg["text"], str(raw_path), voice="Charon", item_type="story", emotion_hint=direction.get("voice_style", ""))
            
            sfx = fetch_sfx(direction["sfx"]) if direction.get("sfx") else ""
            if sfx and os.path.exists(sfx):
                subprocess.run(["ffmpeg", "-y", "-i", str(raw_path), "-stream_loop", "-1", "-i", sfx, "-filter_complex", f"[1:a]volume={SFX_VOLUME}[sfx];[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=2[out]", "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", str(mixed_path)], check=True, capture_output=True)
                add_fade(str(mixed_path), str(fade_path))
            else: add_fade(str(raw_path), str(fade_path))
            
            normalize(str(fade_path), str(norm_path))
            meta = {"index": idx, "type": seg_type, "text": seg["text"], "visual": direction.get("visual", "mosque"), "duration": get_duration(str(norm_path))}
            if seg_type in ("teaser", "outro") and seg.get("next_episode_title"):
                meta["teaser_data"] = {"next_episode_title": seg.get("next_episode_title"), "url": seg.get("url"), "platform_name": "ملاك المعرفة"}
            meta_segments.append(meta)
            parts.append(str(norm_path))

        elif seg_type == "quran":
            surah, ayahs = seg["surah"], seg["ayah_numbers"]
            s_name = seg.get("surah_name", f"سورة {surah}")
            
            if len(ayahs) == 1:
                fetch_ayah_audio(reciter, surah, ayahs[0], str(raw_path))
                txt = fetch_ayah_text(surah, ayahs[0])
                disp = f"{s_name}: {ayahs[0]}"
            else:
                subs = []
                for i, a in enumerate(ayahs):
                    p = work_dir / f"sub_{idx}_{i}.mp3"
                    fetch_ayah_audio(reciter, surah, a, str(p))
                    subs.append(str(p))
                (work_dir / f"list_{idx}.txt").write_text("\n".join([f"file '{s}'" for s in subs]))
                subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / f"list_{idx}.txt"), "-c", "copy", str(raw_path)], check=True)
                disp = f"{s_name}: {ayahs[0]}-{ayahs[-1]}"
                txt = ""
            
            normalize(str(raw_path), str(norm_path))
            meta_segments.append({"index": idx, "type": "quran", "text": txt, "display": disp, "visual": "quran book", "duration": get_duration(str(norm_path))})
            parts.append(str(norm_path))
        idx += 1

    # دمج كل شيء
    (work_dir / "final.txt").write_text("\n".join([f"file '{p}'" for p in parts]))
    merged_raw = work_dir / "merged_raw.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "final.txt"), "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(merged_raw)], check=True)

    # 🌟 تطبيق فلاتر كسر البصمة الصوتية (DSP Fingerprinting Bypass)
    print("🛡️ تطبيق فلاتر كسر البصمة الصوتية...", flush=True)
    
    # الفلاتر:
    # 1. asetrate: يغير النغمة بنسبة 1.5% (غير ملحوظ للأذن)
    # 2. aresample: إعادة المعايرة
    # 3. atempo: يعيد السرعة لتكون أقرب للأصل (مع تسريع طفيف 2%)
    # 4. equalizer: يخفض ترددات 1000Hz و 3000Hz بمقدار 1.5dB و 1dB (لإرباك نظام البصمة)
    dsp_filter = "asetrate=44100*1.015,aresample=44100,atempo=1.02/1.015,equalizer=f=1000:width_type=h:width=200:g=-1.5,equalizer=f=3000:width_type=h:width=500:g=-1"
    
    subprocess.run([
        "ffmpeg", "-y", "-i", str(merged_raw),
        "-af", dsp_filter,
        "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_mp3
    ], check=True, capture_output=True)

    meta_path = output_mp3.replace(".mp3", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_duration(output_mp3), "segments": meta_segments}, f, ensure_ascii=False, indent=2)
    print(f"✅ صوت: {output_mp3} | ⏱️ {get_duration(output_mp3)/60:.1f} دقيقة")

if __name__ == "__main__":
    build_episode_audio(sys.argv[1], sys.argv[2])
