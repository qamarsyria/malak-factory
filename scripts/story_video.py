"""بناء الفيديو: فيديوهات متحركة + تغيير المشاهد + نص متزامن + شعار.
- إصلاح قطع الفيديو (8 دقائق).
- عدم تقطيع الآيات القرآنية.
- إضافة الشعار (Watermark).
- إزالة الخلفية السوداء للنص.
- تغيير المشاهد الطبيعية داخل المقطع الطويل.
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
}

def pick_visual_query(seg: dict, idx: int) -> str:
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

def _fetch_video(query: str, work_dir: Path):
    if not PIXABAY_KEY: return None
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_KEY, "q": query, "video_type": "film", "safesearch": "true", "per_page": 10, "min_width": 1080}, timeout=20)
        if r.status_code != 200: return None
        for hit in r.json().get("hits", []):
            if not is_media_safe(hit.get("tags", "")): continue
            url = hit.get("videos", {}).get("medium", {}).get("url") or hit.get("videos", {}).get("small", {}).get("url")
            if not url: continue
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
            out = work_dir / f"vid_{slug}_{hit['id']}.mp4"
            if _download_file(url, out):
                return str(out)
    except: pass
    return None

def _fetch_image(query: str, work_dir: Path):
    if not PIXABAY_KEY: return None
    try:
        r = requests.get("https://pixabay.com/api/", params={"key": PIXABAY_KEY, "q": query, "image_type": "photo", "orientation": "vertical", "per_page": 10, "safesearch": "true", "min_width": 1080}, timeout=20)
        if r.status_code != 200: return None
        for hit in r.json().get("hits", []):
            if not is_media_safe(hit.get("tags", "")): continue
            url = hit.get("largeImageURL")
            if not url: continue
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
            out = work_dir / f"img_{slug}_{hit['id']}.jpg"
            if _download_file(url, out):
                return str(out)
    except: pass
    return None

def fetch_media(query: str, work_dir: Path):
    local = _find_local(query)
    if local: return local
    video = _fetch_video(query, work_dir)
    if video: return video
    image = _fetch_image(query, work_dir)
    if image: return image
    out = work_dir / "black.jpg"
    if not out.exists(): Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(out)
    return str(out)

def split_text_for_captions(text, max_words=4):
    """تقسيم النص للسرد فقط (الآيات لا تمس)."""
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

def make_caption_overlay(chunk_text, seg_type, out_path):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    
    is_quran = seg_type == "quran"
    color = ACCENT_COLOR if is_quran else TEXT_COLOR
    
    # زيادة حجم الخط قليلاً للآيات لأنها كاملة
    start_sz = 70 if not is_quran else 60
    font, lines, lh = _fit_text(chunk_text, font_path, VIDEO_W - 120, VIDEO_H - 400, start_sz, 45)
    
    total_h = len(lines) * lh
    y = max(200, (VIDEO_H - total_h) // 2)
    
    # إزالة الخلفية السوداء، والاعتماد على الحدود السميكة للحروف
    for line in lines:
        _draw_centered(draw, y, line, font, color, 5, "black") # زيادة سماكة الحدود لـ 5
        y += lh
        
    # إضافة الشعار (Watermark) في كل صورة
    try:
        fw = ImageFont.truetype(font_path, 35, layout_engine=ImageFont.Layout.RAQM)
    except:
        fw = ImageFont.truetype(font_path, 35)
    wm = _shape("ملاك المعرفة")
    bbox = draw.textbbox((0, 0), wm, font=fw)
    draw.text((VIDEO_W - (bbox[2] - bbox[0]) - 40, VIDEO_H - 100), wm, font=fw, fill=(255, 215, 0, 200), stroke_width=2, stroke_fill="black")
        
    overlay.save(out_path)

def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_vid_"))
    concat_lines = []

    try:
        for i, seg in enumerate(meta["segments"]):
            duration = seg["duration"]
            if duration <= 0: continue
            
            seg_type = seg.get("type", "narration")
            print(f"🎬 مقطع {i+1}: {seg_type} ({duration:.1f}s)", flush=True)
            
            # 1. تجهيز النصوص (الآيات كاملة، السرد مقسم)
            text = seg.get("text", "")
            if seg_type == "quran":
                chunks = [text] # الآية كاملة
            else:
                chunks = split_text_for_captions(text, max_words=4)
            
            if not chunks: chunks = [""]
            time_per_chunk = duration / len(chunks)
            
            # 2. تجهيز الخلفية (تغيير المشهد كل 15 ثانية للمقاطع الطويلة)
            num_scenes = max(1, math.ceil(duration / 15))
            scene_duration = duration / num_scenes
            scene_files = []
            
            for s_idx in range(num_scenes):
                query = pick_visual_query(seg, i)
                media = fetch_media(query, work_dir)
                # إنشاء فيديو قصير للمشهد
                scene_out = work_dir / f"scene_{i}_{s_idx}.mp4"
                subprocess.run([
                    "ffmpeg", "-y", "-stream_loop", "-1", "-i", media,
                    "-t", str(scene_duration), "-r", "24",
                    "-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,crop={VIDEO_W}:{VIDEO_H}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    str(scene_out)
                ], check=True, capture_output=True)
                scene_files.append(scene_out)

            # دمج المشاهد لصنع خلفية المقطع
            bg_concat = work_dir / f"bg_cat_{i}.txt"
            bg_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in scene_files), encoding="utf-8")
            bg_video = work_dir / f"bg_vid_{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(bg_concat), "-c", "copy", str(bg_video)], check=True, capture_output=True)

            # 3. وضع النصوص فوق الخلفية
            cmd = ["ffmpeg", "-y", "-i", str(bg_video)]
            filter_parts = ["[0:v]setpts=PTS-STARTPTS[bg0]"]
            
            current_video = "[bg0]"
            input_idx = 1
            
            for c_idx, chunk in enumerate(chunks):
                if not chunk.strip(): continue
                
                overlay_png = work_dir / f"ov_{i}_{c_idx}.png"
                make_caption_overlay(chunk, seg_type, overlay_png)
                
                cmd.extend(["-i", str(overlay_png)])
                
                start_t = c_idx * time_per_chunk
                end_t = (c_idx + 1) * time_per_chunk
                
                next_video = f"[v{c_idx}]"
                filter_parts.append(f"{current_video}[{input_idx}:v]overlay=enable='between(t,{start_t},{end_t})'{next_video}")
                current_video = next_video
                input_idx += 1
            
            out_seg = work_dir / f"seg_{i}.mp4"
            cmd.extend([
                "-filter_complex", ";".join(filter_parts),
                "-map", current_video,
                "-t", str(duration), "-r", "24",
                "-c:v", "libx264", "-preset", "faster", "-crf", "32", # ضغط أعلى لتقليل الحجم
                str(out_seg)
            ])
            
            subprocess.run(cmd, check=True, capture_output=True)
            concat_lines.append(f"file '{out_seg}'")

        # 4. الدمج النهائي (بدون -shortest لضمان اكتمال الفيديو)
        (work_dir / "final_concat.txt").write_text("\n".join(concat_lines), encoding="utf-8")
        print("🔗 الدمج النهائي مع الصوت...", flush=True)
        
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "final_concat.txt"),
            "-i", audio_path, 
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_mp4
        ], check=True, capture_output=True)
        
        size_mb = Path(output_mp4).stat().st_size / (1024 * 1024)
        print(f"✅ فيديو: {output_mp4} ({size_mb:.1f} MB)", flush=True)
    finally:
        cleanup_dir(work_dir)

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(1)
    build_video(sys.argv[1], sys.argv[2], sys.argv[3])
