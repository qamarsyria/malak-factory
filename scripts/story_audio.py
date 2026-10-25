import os, subprocess, tempfile, json, sys, random
from pathlib import Path
import requests
from config import QURAN_API
from voice import generate as gemini_generate
from smart_director import direct_scene
from sfx_manager import fetch_sfx

TARGET_BR = "192k"
SFX_VOLUME = 0.05

def normalize(input_path, output_path):
    subprocess.run(["ffmpeg", "-y", "-i", input_path, "-ar", "44100", "-ac", "1", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_path], check=True, capture_output=True)

def fetch_ayah_text(surah, ayah):
    try:
        r = requests.get(f"{QURAN_API}/ayah/{surah}:{ayah}/ar.alafasy", timeout=10)
        if r.status_code == 200: 
            return r.json()['data']['text']
    except Exception as e: 
        print(f"   ⚠️ خطأ في جلب نص الآية: {e}", flush=True)
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

    for seg in ep.get("segments", []):
        seg_type = seg.get("type")
        raw_path = work_dir / f"raw_{idx:03d}.mp3"
        mixed_path = work_dir / f"mixed_{idx:03d}.mp3"
        fade_path = work_dir / f"fade_{idx:03d}.mp3"
        norm_path = work_dir / f"norm_{idx:03d}.mp3"

        # ══════════════════════════════════════════════════════════
        # 1. السرد والأووتر والتشويق (صوت Gemini)
        # ══════════════════════════════════════════════════════════
        if seg_type in ("narration", "teaser", "outro"):
            direction = {"visual": "mosque", "sfx": "", "voice_style": seg["emotion_hint"]} if seg.get("emotion_hint") else direct_scene(seg["text"])
            gemini_generate(seg["text"], str(raw_path), voice="Charon", item_type="story", emotion_hint=direction.get("voice_style", ""))
            
            sfx = fetch_sfx(direction["sfx"]) if direction.get("sfx") else ""
            if sfx and os.path.exists(sfx):
                subprocess.run(["ffmpeg", "-y", "-i", str(raw_path), "-stream_loop", "-1", "-i", sfx, "-filter_complex", f"[1:a]volume={SFX_VOLUME}[sfx];[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=2[out]", "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", str(mixed_path)], check=True, capture_output=True)
                add_fade(str(mixed_path), str(fade_path))
            else: 
                add_fade(str(raw_path), str(fade_path))
            
            normalize(str(fade_path), str(norm_path))
            meta = {"index": idx, "type": seg_type, "text": seg["text"], "visual": direction.get("visual", "mosque"), "duration": get_duration(str(norm_path))}
            if seg_type in ("teaser", "outro") and seg.get("next_episode_title"):
                meta["teaser_data"] = {"next_episode_title": seg.get("next_episode_title"), "url": seg.get("url"), "platform_name": "ملاك المعرفة"}
            meta_segments.append(meta)
            parts.append(str(norm_path))

        # ══════════════════════════════════════════════════════════
        # 2. الآيات القرآنية (نص من alquran.cloud + صوت من Gemini)
        # ══════════════════════════════════════════════════════════
        elif seg_type == "quran":
            surah, ayahs = seg["surah"], seg["ayah_numbers"]
            s_name = seg.get("surah_name", f"سورة {surah}")
            
            # جلب النص المثالي 100%
            if len(ayahs) == 1:
                txt = fetch_ayah_text(surah, ayahs[0])
                disp = f"{s_name}: {ayahs[0]}"
            else:
                txt_list = []
                for a in ayahs:
                    txt_list.append(fetch_ayah_text(surah, a))
                txt = " ".join(txt_list)
                disp = f"{s_name}: {ayahs[0]}-{ayahs[-1]}"
                
            print(f"[{idx}] آية بصوت Gemini: {disp}...", flush=True)
            
            if txt:
                # توليد الصوت عبر Gemini (مع طلب نبرة خشوع وتجويد)
                gemini_generate(txt, str(raw_path), voice="Charon", item_type="quran", emotion_hint="(in a solemn, respectful, slow, and clear tone with proper tajweed)")
                normalize(str(raw_path), str(norm_path))
                
                meta_segments.append({
                    "index": idx, "type": "quran", "text": txt, "display": disp, 
                    "visual": "quran book", "duration": get_duration(str(norm_path))
                })
                parts.append(str(norm_path))
            else:
                print(f"   ⚠️ فشل جلب نص الآية {disp}، سيتم تخطيها.", flush=True)
        idx += 1

    (work_dir / "final.txt").write_text("\n".join([f"file '{p}'" for p in parts]))
    merged_raw = work_dir / "merged_raw.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "final.txt"), "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(merged_raw)], check=True)

    # 🌟 فلاتر كسر البصمة (احتياط إضافي)
    print("🛡️ تطبيق فلاتر كسر البصمة الصوتية...", flush=True)
    dsp_filter = "asetrate=44100*1.015,aresample=44100,atempo=1.02/1.015,equalizer=f=1000:width_type=h:width=200:g=-1.5,equalizer=f=3000:width_type=h:width=500:g=-1"
    subprocess.run(["ffmpeg", "-y", "-i", str(merged_raw), "-af", dsp_filter, "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_mp3], check=True, capture_output=True)

    meta_path = output_mp3.replace(".mp3", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_duration(output_mp3), "segments": meta_segments}, f, ensure_ascii=False, indent=2)
    print(f"✅ صوت: {output_mp3} | ⏱️ {get_duration(output_mp3)/60:.1f} دقيقة")

if __name__ == "__main__":
    build_episode_audio(sys.argv[1], sys.argv[2])
