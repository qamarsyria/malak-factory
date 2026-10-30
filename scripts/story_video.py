"""بناء الفيديو: فيديوهات متحركة متغيرة + نصوص ديناميكية + شعار.
- إصلاح جذري لانهيار ffmpeg (Exit code 234) عبر المعالجة المتسلسلة وتخفيف الكوديك.
- إزالة أي حروف إنجليزية أو فيديوهات تحتوي على نصوص.
- حل مشكلة انقطاع الفيديو قبل الصوت باستخدام tpad.
- إصلاح تزامن النص مع الصوت بحساب عدد الكلمات وعلامات الترقيم.
"""
import os
import re
import sys
import json
import math
import subprocess
import tempfile
import random
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W,
    ACCENT_COLOR, PIXABAY_KEY, BG_VIDEOS_DIR, BG_IMAGES_DIR,
)
from video import _find_arabic_font, _shape, _wrap
from utilities import cleanup_dir, notify_telegram

FORBIDDEN_TAGS = {
    "woman", "women", "girl", "female", "child", "boy", "man", "men",
    "people", "person", "face", "human", "body", "hand", "skin",
    "crowd", "family", "couple", "wedding", "dance", "dancing",
    "idol", "church", "cross", "crucifix",
    "alcohol", "wine", "beer", "drunk", "pig", "dog", "bride", "groom",
    "nude", "naked", "bikini", "lingerie", "kiss", "romance",
    "text", "word", "words", "quote", "quotes", "letter", "letters", 
    "typography", "font", "english", "alphabet", "watermark", "logo"
}

def is_media_safe(tags_str: str) -> bool:
    tags = {t.strip().lower() for t in str(tags_str).replace(",", " ").split()}
    return not (tags & FORBIDDEN_TAGS)

KEYWORD_MAP = {
    "بحر": "sea waves ocean", "يم": "sea waves ocean", "موج": "ocean waves",
    "نهر": "river flowing", "ماء": "water flowing", "بئر": "stone well water",
    "شلال": "waterfall nature", "مطر": "rain falling", "غيوم": "clouds moving sky",
    "سحاب": "clouds sky", "رعد": "thunder storm", "برق": "lightning storm",
    "جبل": "mountain peak", "طور": "mountain peak", "صخر": "rocky mountain",
    "صحراء": "desert dunes", "رمل": "sand dunes", "واد": "valley nature",
    "أرض": "landscape nature", "نخل": "palm trees", "شجر": "trees forest",
    "بستان": "garden trees", "جنة": "garden paradise", "زرع": "wheat field",
    "ثمر": "fruit tree", "نجوم": "starry night sky", "نجم": "stars night",
    "قمر": "moon night", "شمس": "sunrise sky", "فجر": "sunrise dawn",
    "ليل": "night sky dark", "ظلام": "dark night", "سماء": "sky clouds",
    "بيت": "ancient stone house", "مدينة": "ancient city", "قصر": "ancient palace",
    "محراب": "mosque interior", "مسجد": "mosque architecture", "كعبة": "kaaba mecca",
    "مكة": "mecca ancient", "بيت المقدس": "jerusalem dome", "طير": "birds flying sky",
    "طيور": "birds flying", "جمل": "camel desert", "خيل": "horse running",
    "غنم": "sheep pasture", "حوت": "whale ocean", "نحل": "bees flowers",
    "نار": "fire flames", "لهب": "fire flames", "ريح": "wind trees",
    "عاصفة": "storm clouds", "زلزال": "desert cracked", "ظل": "shadow trees",
}

POOLS_BY_TYPE = {
    "quran": ["quran book", "islamic calligraphy", "mosque interior"],
    "narration": ["desert dunes", "starry night sky", "mountain peak", "river flowing", "palm trees oasis", "clouds moving sky", "ancient stone ruins", "sunrise dawn"],
    "summary": ["sunrise sky", "calm sea", "mountain peak", "starry night"],
    "teaser": ["dramatic clouds", "starry night sky", "moon night"],
    "cta": ["mosque architecture", "islamic pattern", "mosque minaret"],
    "intro": ["mosque architecture", "islamic pattern"],
    "outro": ["mosque architecture", "sunrise sky"],
}

def get_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=15)
    try: return float(r.stdout.strip())
    except: return 0.0

def pick_visual_query(seg: dict) -> str:
    explicit = seg.get("visual", "").strip()
    if explicit and explicit not in ("mosque", "quran book", "islamic architecture"):
        return explicit
    text = seg.get("text", "")
    for keyword, query in KEYWORD_MAP.items():
        if keyword in text:
            return query
    seg_type = seg.get("type", "narration")
    pool = POOLS_BY_TYPE.get(seg_type, POOLS_BY_TYPE["narration"])
    return random.choice(pool)

def _find_local(query: str):
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    for base in (Path(BG_VIDEOS_DIR), Path(BG_IMAGES_DIR)):
        if not base.exists(): continue
        for f in base.glob(f"{slug}*"):
            if f.suffix.lower() in (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png") and f.stat().st_size > 10000:
                return str(f)
    return None

def _download_file(url: str, out_path: Path) -> bool:
    try:
        r = requests.get(url, timeout=60, stream=True)
        if r.status_code != 200: return False
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192): f.write(chunk)
        return out_path.stat().st_size > 20000
    except: return False

def _fetch_video(query: str, work_dir: Path, used_urls: set):
    if not PIXABAY_KEY: return None
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_KEY, "q": query, "video_type": "film", "safesearch": "true", "per_page": 20, "min_width": 1080}, timeout=20)
        if r.status_code != 200: return None
        random.shuffle(r.json().get("hits", []))
        for hit in r.json().get("hits", []):
            if not is_media_safe(hit.get("tags", "")): continue
            url = hit.get("videos", {}).get("medium", {}).get("url") or hit.get("videos", {}).get("small", {}).get("url")
            if not url or url in used_urls: continue
            
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
            out = work_dir / f"vid_{slug}_{hit['id']}.mp4"
            if _download_file(url, out):
                used_urls.add(url)
                return str(out)
    except Exception: pass
    return None

def _fetch_image(query: str, work_dir: Path, used_urls: set):
    if not PIXABAY_KEY: return None
    try:
        r = requests.get("https://pixabay.com/api/", params={"key": PIXABAY_KEY, "q": query, "image_type": "photo", "orientation": "vertical", "per_page": 20, "safesearch": "true", "min_width": 1080}, timeout=20)
        if r.status_code != 200: return None
        random.shuffle(r.json().get("hits", []))
        for hit in r.json().get("hits", []):
            if not is_media_safe(hit.get("tags", "")): continue
            url = hit.get("largeImageURL")
            if not url or url in used_urls: continue
            
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
            out = work_dir / f"img_{slug}_{hit['id']}.jpg"
            if _download_file(url, out):
                used_urls.add(url)
                return str(out)
    except Exception: pass
    return None

def fetch_media(query: str, work_dir: Path, used_urls: set):
    local = _find_local(query)
    if local: return local
    video = _fetch_video(query, work_dir, used_urls)
    if video: return video
    image = _fetch_image(query, work_dir, used_urls)
    if image: return image
    out = work_dir / "black.jpg"
    if not out.exists(): Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(out)
    return str(out)

def split_text_for_captions(text, max_words=4, is_quran=False):
    if is_quran: return [text]
    words = text.split()
    chunks, current = [], []
    for w in words:
        current.append(w)
        if len(current) >= max_words or any(p in w for p in [".", "؟", "!", "،"]):
            chunks.append(" ".join(current))
            current = []
    if current: chunks.append(" ".join(current))
    return chunks

def _fit_text(text, font_path, max_width, max_height, start_size, min_size):
    dummy = Image.new("RGB", (10, 10))
    ddraw = ImageDraw.Draw(dummy)
    for size in range(start_size, min_size - 1, -2):
        try: font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
        except: font = ImageFont.truetype(font_path, size)
        lines = _wrap(_shape(text), font, max_width, ddraw)
        if len(lines) * int(size * 1.6) <= max_height:
            return font, lines, int(size * 1.6)
    font = ImageFont.truetype(font_path, min_size)
    return font, _wrap(_shape(text), font, max_width, ddraw), int(min_size * 1.6)

def _draw_centered(draw, y, text, font, fill, stroke_w, stroke_fill):
    shaped = _shape(text)
    bbox = draw.textbbox((0, 0), shaped, font=font)
    x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), shaped, font=font, fill=fill, stroke_width=stroke_w, stroke_fill=stroke_fill)

def make_caption_overlay(chunk_text, seg, out_path):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    
    chunk_text = re.sub(r'[a-zA-Z]', '', chunk_text).strip()
    if not chunk_text and seg.get("type") != "quran": 
        overlay.save(out_path)
        return

    is_quran = seg.get("type") == "quran"
    color = ACCENT_COLOR if is_quran else TEXT_COLOR
    
    display_text = chunk_text
    if is_quran and seg.get("display"):
        display_text += f"\n﴿ {seg['display']} ﴾"
        
    font, lines, lh = _fit_text(display_text, font_path, VIDEO_W - 120, VIDEO_H - 400, 70, 45)
    
    total_h = len(lines) * lh
    y = max(200, (VIDEO_H - total_h) // 2)
    
    stroke_width = 6 if not is_quran else 8
    for i, line in enumerate(lines):
        line_color = color if i < len(lines)-1 or not is_quran else "#D4AF37"
        _draw_centered(draw, y, line, font, line_color, stroke_width, "black")
        y += lh
        
    try: wm_font = ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.RAQM)
    except: wm_font = ImageFont.truetype(font_path, 40)
    wm_text = _shape("ملاك المعرفة")
    wm_bbox = draw.textbbox((0,0), wm_text, font=wm_font)
    wm_x = VIDEO_W - (wm_bbox[2] - wm_bbox[0]) - 40
    wm_y = VIDEO_H - 80
    draw.text((wm_x, wm_y), wm_text, font=wm_font, fill="#D4AF37", stroke_width=4, stroke_fill="black")
        
    overlay.save(out_path)

def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_vid_"))
    concat_lines = []
    used_urls = set()

    try:
        for i, seg in enumerate(meta["segments"]):
            duration = seg["duration"]
            if duration <= 0: continue
            
            print(f"🎬 مقطع {i+1}: {seg['type']} ({duration:.1f}s)", flush=True)
            
            # 1. بناء فيديو الخلفية (بشكل متسلسل)
            num_bgs = max(1, math.ceil(duration / 10))
            bg_files = []
            for _ in range(num_bgs):
                query = pick_visual_query(seg)
                bg_files.append(fetch_media(query, work_dir, used_urls))
            
            bg_duration = duration / num_bgs
            processed_bgs = []
            
            for b_idx, bg in enumerate(bg_files):
                temp_bg = work_dir / f"bg_{i}_{b_idx}.mp4"
                is_img = bg.endswith(('.jpg', '.jpeg', '.png'))
                
                cmd_bg = ["ffmpeg", "-y"]
                if is_img:
                    cmd_bg.extend(["-loop", "1", "-i", bg, "-t", str(bg_duration)])
                else:
                    cmd_bg.extend(["-stream_loop", "-1", "-i", bg, "-t", str(bg_duration)])
                
                cmd_bg.extend([
                    "-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,crop={VIDEO_W}:{VIDEO_H},fps=24,format=yuv420p,setsar=1",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
                    str(temp_bg)
                ])
                subprocess.run(cmd_bg, check=True, capture_output=True)
                processed_bgs.append(temp_bg)
                
            bg_concat_txt = work_dir / f"bg_concat_{i}.txt"
            with open(bg_concat_txt, "w") as f:
                for p_bg in processed_bgs:
                    f.write(f"file '{p_bg.resolve()}'\n")
                    
            bg_video_path = work_dir / f"bg_{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(bg_concat_txt), "-c", "copy", str(bg_video_path)], check=True, capture_output=True)

            # 2. بناء فيديو النصوص (شفاف)
            is_quran = seg.get("type") == "quran"
            text = seg.get("text", "")
            chunks = split_text_for_captions(text, max_words=4, is_quran=is_quran)
            if not chunks: chunks = [""]
            
            chunk_weights = []
            for c in chunks:
                words_count = len(c.split())
                punct_count = len(re.findall(r'[.،؟!]', c))
                weight = words_count + (punct_count * 1.5)
                chunk_weights.append(max(1, weight))
                
            total_weight = sum(chunk_weights)
            
            text_inputs = []
            text_filters = []
            actual_chunks = 0
            
            for c_idx, chunk in enumerate(chunks):
                if not chunk.strip(): continue
                
                overlay_png = work_dir / f"ov_{i}_{c_idx}.png"
                make_caption_overlay(chunk, seg, overlay_png)
                
                text_inputs.extend(["-loop", "1", "-i", str(overlay_png)])
                
                chunk_duration = duration * (chunk_weights[c_idx] / total_weight)
                
                text_filters.append(
                    f"[{actual_chunks}:v]trim=start=0:end={chunk_duration},setpts=PTS-STARTPTS,format=rgba[t{actual_chunks}]"
                )
                actual_chunks += 1
            
            text_video_path = work_dir / f"text_{i}.mov"
            
            if actual_chunks == 0:
                # إذا لم يكن هناك نصوص، ننشئ فيديو شفاف
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black@0.0:s={VIDEO_W}x{VIDEO_H}:d={duration},format=rgba",
                    "-c:v", "qtrle", "-pix_fmt", "rgba", str(text_video_path)
                ], check=True, capture_output=True)
            else:
                text_concat = "".join(f"[t{k}]" for k in range(actual_chunks))
                text_filters.append(f"{text_concat}concat=n={actual_chunks}:v=1:a=0[text]")
                
                text_cmd = ["ffmpeg", "-y"] + text_inputs + [
                    "-filter_complex", ";".join(text_filters),
                    "-map", "[text]", "-t", str(duration),
                    "-c:v", "qtrle", "-pix_fmt", "rgba",
                    str(text_video_path)
                ]
                subprocess.run(text_cmd, check=True, capture_output=True)

            # 3. دمج الخلفية مع النص
            out_seg = work_dir / f"seg_{i}.mp4"
            final_cmd = [
                "ffmpeg", "-y", "-i", str(bg_video_path), "-i", str(text_video_path),
                "-filter_complex", "[0:v][1:v]overlay",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
                str(out_seg)
            ]
            subprocess.run(final_cmd, check=True, capture_output=True)
            
            concat_lines.append(f"file '{out_seg}'")

        audio_dur = get_duration(audio_path)
        (work_dir / "concat.txt").write_text("\n".join(concat_lines), encoding="utf-8")
        
        print("🔗 الدمج النهائي ومزامنة الصوت مع الفيديو...", flush=True)
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "concat.txt"),
            "-i", audio_path,
            "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={audio_dur}[v]",
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(audio_dur),
            str(output_mp4)
        ], check=True, capture_output=True)
        
        print(f"✅ فيديو: {output_mp4}", flush=True)
    finally:
        cleanup_dir(work_dir)

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(1)
    build_video(sys.argv[1], sys.argv[2], sys.argv[3])
