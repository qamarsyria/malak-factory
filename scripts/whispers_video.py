import os, sys, re, json, subprocess, tempfile, random, requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import arabic_reshaper
from config import VIDEO_W, VIDEO_H, PIXABAY_KEY
from utilities import cleanup_dir

NUM_SCENES = 3
CROSSFADE = 1.5  # ثواني الانتقال بين المشاهد

# ═══════════════════════════════════════════════════════════
# 🌟 كلمات البحث (فخمة ومتنوعة)
# ═══════════════════════════════════════════════════════════
def get_queries(category):
    if category == "romantic":
        return [
            # بنات منقبات وزهور
            "niqab woman flowers", "muslim woman garden",
            # طبيعة رومانسية
            "butterflies flowers romantic", "rose petals falling",
            "red roses garden cinematic", "pink flowers sunset",
            # فخامة
            "silk fabric flowing", "candles bokeh warm",
            "fairy lights bokeh", "golden bokeh lights",
            # خيال
            "fantasy magical forest", "fairy tale castle",
            # قطط
            "kitten cute playing", "cat flowers garden",
        ]
    else:  # dua
        return [
            "golden clouds sunset", "starry night milky way",
            "islamic mosque cinematic", "candle light dark",
            "islamic architecture golden", "praying hands soft",
            "clouds golden hour", "moonlight serene",
        ]

# ═══════════════════════════════════════════════════════════
# 🌟 جلب فيديو من Pixabay
# ═══════════════════════════════════════════════════════════
def fetch_video(query, work_dir, used_urls):
    if not PIXABAY_KEY: return None
    try:
        r = requests.get("https://pixabay.com/api/videos/", 
                        params={"key": PIXABAY_KEY, "q": query, "video_type": "film", "per_page": 20}, 
                        timeout=20)
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            random.shuffle(hits)
            for hit in hits:
                url = hit.get("videos", {}).get("medium", {}).get("url")
                if url and url not in used_urls:
                    out = work_dir / f"vid_{hit['id']}.mp4"
                    with open(out, "wb") as f:
                        for chunk in requests.get(url, timeout=60, stream=True).iter_content(8192):
                            f.write(chunk)
                    used_urls.add(url)
                    return str(out)
    except: pass
    return None

# ═══════════════════════════════════════════════════════════
# 🌟 إنشاء خلفية ذهبية احتياطية
# ═══════════════════════════════════════════════════════════
def make_golden_fallback(path):
    img = Image.new("RGB", (VIDEO_W, VIDEO_H))
    draw = ImageDraw.Draw(img)
    for y in range(VIDEO_H):
        ratio = y / VIDEO_H
        r = int(10 + (30 - 10) * ratio)
        g = int(15 + (40 - 15) * ratio)
        b = int(40 + (70 - 40) * ratio)
        draw.line([(0, y), (VIDEO_W, y)], fill=(r, g, b))
    img.save(path, "JPEG", quality=90)
    return str(path)

# ═══════════════════════════════════════════════════════════
# 🌟 جزيئات بوكيه ذهبية (الطبقة الفخمة)
# ═══════════════════════════════════════════════════════════
def create_bokeh_overlay():
    W, H = VIDEO_W + 100, VIDEO_H + 100  # أكبر عشان نتحركها
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    for _ in range(120):
        x = random.randint(0, W)
        y = random.randint(0, H)
        r = random.randint(15, 70)
        alpha = random.randint(40, 180)
        color = random.choice([
            (255, 215, 0),   # ذهبي
            (255, 230, 150), # ذهبي فاتح
            (212, 175, 55),  # ذهبي غامق
            (255, 200, 50),  # ذهبي دافئ
        ])
        draw.ellipse([x-r, y-r, x+r, y+r], fill=(*color, alpha))
    
    img = img.filter(ImageFilter.GaussianBlur(radius=8))
    return img

# ═══════════════════════════════════════════════════════════
# 🌟 اسم بإطار ذهبي فخم
# ═══════════════════════════════════════════════════════════
def create_name_overlay(name):
    if not name: return None
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    font_path = None
    for p in ["/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf",
              "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
              "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"]:
        if os.path.exists(p):
            font_path = p; break
    if not font_path: return None
    
    shaped = arabic_reshaper.reshape(name)
    font = ImageFont.truetype(font_path, 200, layout_engine=ImageFont.Layout.RAQM)
    
    bbox = draw.textbbox((0, 0), shaped, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (VIDEO_W - tw) // 2
    y = (VIDEO_H - th) // 2
    
    # ظل قوي
    draw.text((x+5, y+5), shaped, font=font, fill="black", stroke_width=10, stroke_fill="black")
    # النص الذهبي
    draw.text((x, y), shaped, font=font, fill="#FFD700", stroke_width=5, stroke_fill="#8B6914")
    
    # إطار ذهبي فخم
    pad_x = 60
    pad_y = 40
    fx1, fy1 = x - pad_x, y - pad_y
    fx2, fy2 = x + tw + pad_x, y + th + pad_y
    
    # خطين علوي وسفلي
    draw.line([(fx1, fy1), (fx2, fy1)], fill="#FFD700", width=4)
    draw.line([(fx1, fy2), (fx2, fy2)], fill="#FFD700", width=4)
    
    # زخرفة في الأطراف
    for px, py in [(fx1, fy1), (fx2, fy1), (fx1, fy2), (fx2, fy2)]:
        draw.ellipse([px-8, py-8, px+8, py+8], fill="#FFD700")
    
    return overlay

# ═══════════════════════════════════════════════════════════
# 🌟 بناء الفيديو الفخم
# ═══════════════════════════════════════════════════════════
def build_whisper_video(audio_path, json_path, out_mp4):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    category = ep.get("category", "romantic")
    name = ep.get("target_name", "")
    
    work_dir = Path(tempfile.mkdtemp(prefix="whisper_vid_"))
    
    # مدة الصوت
    dur_out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path], capture_output=True, text=True).stdout.strip()
    duration = float(dur_out or 10.0)
    print(f"⏱️  المدة: {duration:.1f} ثانية")
    
    # جلب مشاهد متنوعة
    queries = get_queries(category)
    random.shuffle(queries)
    used_urls = set()
    scene_paths = []
    
    for q in queries:
        if len(scene_paths) >= NUM_SCENES: break
        v = fetch_video(q, work_dir, used_urls)
        if v:
            scene_paths.append(v)
            print(f"   ✅ مشهد: {q}")
    
    # ملء الباقي بخلفيات ذهبية
    while len(scene_paths) < NUM_SCENES:
        fb = work_dir / f"fallback_{len(scene_paths)}.jpg"
        make_golden_fallback(fb)
        scene_paths.append(str(fb))
    
    # حساب مدة كل مشهد
    scene_dur = (duration + (NUM_SCENES - 1) * CROSSFADE) / NUM_SCENES
    print(f"🎬 {NUM_SCENES} مشاهد × {scene_dur:.1f} ثانية")
    
    # تجهيز كل مشهد: قص + تدرج لوني دافئ + فينييت
    processed = []
    for i, sp in enumerate(scene_paths):
        out = work_dir / f"scene_{i}.mp4"
        is_img = sp.endswith(('.jpg', '.jpeg', '.png'))
        input_flags = ["-loop", "1"] if is_img else ["-stream_loop", "-1"]
        
        vf = (
            f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H},fps=24,format=yuv420p,"
            f"colorbalance=rs=0.12:gs=0.04:bs=-0.08,"  # دفء ذهبي
            f"eq=brightness=0.02:contrast=1.1:saturation=1.15,"
            f"vignette=PI/5"  # تعتيم الحواف للفخامة
        )
        cmd = ["ffmpeg", "-y"] + input_flags + [
            "-i", sp, "-t", str(scene_dur),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "faster", "-crf", "30", "-b:v", "800k",
            str(out)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        processed.append(str(out))
    
    # دمج المشاهد بـ crossfade ناعم
    bg_video = work_dir / "bg.mp4"
    if len(processed) == 1:
        subprocess.run(["ffmpeg", "-y", "-i", processed[0], "-t", str(duration), "-c:v", "copy", str(bg_video)], check=True, capture_output=True)
    else:
        inputs = []
        for p in processed: inputs.extend(["-i", p])
        
        filter_parts = []
        last = "[0:v]"
        offset = 0
        for i in range(1, len(processed)):
            offset += scene_dur - CROSSFADE
            new = f"[x{i}]"
            filter_parts.append(f"{last}[{i}:v]xfade=transition=fade:duration={CROSSFADE}:offset={offset:.3f}{new}")
            last = new
        
        filter_str = ";".join(filter_parts)
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_str, "-map", last,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "faster", "-crf", "30", "-b:v", "800k",
            str(bg_video)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    
    # طبقة البوكيه
    bokeh_img = create_bokeh_overlay()
    bokeh_path = work_dir / "bokeh.png"
    bokeh_img.save(bokeh_path)
    
    # الاسم
    name_img = create_name_overlay(name)
    name_path = None
    if name_img:
        name_path = work_dir / "name.png"
        name_img.save(name_path)
    
    # الدمج النهائي
    final_vid = work_dir / "final_vid.mp4"
    
    if name_path:
        filter_complex = (
            f"[1:v]format=rgba,colorchannelmixer=aa=0.5[bk];"
            f"[0:v][bk]overlay=0:0:shortest=1[v1];"
            f"[v1][2:v]overlay=0:0,format=yuv420p[vout]"
        )
        cmd = ["ffmpeg", "-y", "-i", str(bg_video), "-loop", "1", "-i", str(bokeh_path), "-loop", "1", "-i", str(name_path),
               "-filter_complex", filter_complex, "-map", "[vout]", "-t", str(duration),
               "-c:v", "libx264", "-preset", "faster", "-crf", "30", "-b:v", "800k", str(final_vid)]
    else:
        filter_complex = (
            f"[1:v]format=rgba,colorchannelmixer=aa=0.5[bk];"
            f"[0:v][bk]overlay=0:0:shortest=1,format=yuv420p[vout]"
        )
        cmd = ["ffmpeg", "-y", "-i", str(bg_video), "-loop", "1", "-i", str(bokeh_path),
               "-filter_complex", filter_complex, "-map", "[vout]", "-t", str(duration),
               "-c:v", "libx264", "-preset", "faster", "-crf", "30", "-b:v", "800k", str(final_vid)]
    subprocess.run(cmd, check=True, capture_output=True)
    
    # دمج الصوت
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final_vid), "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
        "-t", str(duration), str(out_mp4)
    ], check=True, capture_output=True)
    
    size_mb = os.path.getsize(out_mp4) / (1024 * 1024)
    print(f"✅ فيديو: {out_mp4} | {size_mb:.1f}MB | مشاهد: {len(processed)} | الاسم: {name or 'عام'}")
    cleanup_dir(work_dir)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(1)
    build_whisper_video(sys.argv[1], sys.argv[2], sys.argv[3])
