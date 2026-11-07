import os, sys, re, json, subprocess, tempfile, random, requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import arabic_reshaper
from config import VIDEO_W, VIDEO_H, PIXABAY_KEY
from utilities import cleanup_dir

NUM_SCENES = 4
CROSSFADE = 1.2  # ثواني الانتقال بين المشاهد

# ═══════════════════════════════════════════════════════════
# 🌟 بنك البحث (آمن 100% - رومانسي وإسلامي)
# ═══════════════════════════════════════════════════════════
def get_queries(category):
    if category == "romantic":
        return [
            # رسوم متحركة آمنة
            ("rose flower animation", "animation"),
            ("heart love animation", "animation"),
            ("butterfly flowers animation", "animation"),
            ("pink roses blooming", "animation"),
            # مشاهد حقيقية رومانسية
            ("niqab woman roses", "film"),
            ("muslim woman flowers garden", "film"),
            ("red roses bouquet cinematic", "film"),
            ("pink roses soft light", "film"),
            ("butterflies garden flowers", "film"),
            ("rose petals falling slow", "film"),
            ("silk fabric soft", "film"),
            ("candles bokeh warm", "film"),
            ("kitten flowers cute", "film"),
        ]
    else:  # dua
        return [
            ("islamic mosque golden", "film"),
            ("kaaba cinematic sunset", "film"),
            ("quran book soft light", "film"),
            ("praying hands candles", "film"),
            ("starry night milky way", "film"),
            ("golden clouds sunset", "film"),
            ("islamic lantern glowing", "film"),
            ("moonlight serene", "film"),
            ("rose flower animation", "animation"),
            ("golden bokeh lights", "animation"),
        ]

# ═══════════════════════════════════════════════════════════
# 🌟 فلترة صارمة (Blacklist + Whitelist)
# ═══════════════════════════════════════════════════════════
def is_video_safe(tags, category):
    tags_lower = tags.lower()
    
    # قائمة سوداء (ممنوعة تماماً)
    blacklist = [
        "skeleton", "skull", "bone", "robot", "cyborg", "alien", 
        "zombie", "horror", "scary", "creepy", "monster", "demon", 
        "ghost", "devil", "satan", "occult", "gore", "blood", 
        "war", "military", "weapon", "gun", "bomb", "fight",
        "death", "dead", "graveyard", "cemetery", "haunted", "witch",
        "vampire", "werewolf", "clown", "snake", "spider", "dark"
    ]
    if any(bad in tags_lower for bad in blacklist):
        return False
    
    # قائمة بيضاء (لازم كلمة واحدة على الأقل)
    if category == "romantic":
        whitelist = [
            "rose", "flower", "butterfly", "love", "romantic", "heart", 
            "garden", "petal", "cute", "beautiful", "soft", "pink", 
            "red", "niqab", "hijab", "bouquet", "bloom", "romance",
            "silk", "candle", "bokeh", "sweet", "kitten", "cat"
        ]
        return any(good in tags_lower for good in whitelist)
    else:  # dua
        whitelist = [
            "mosque", "islamic", "prayer", "quran", "kaaba", "moon",
            "star", "cloud", "sky", "golden", "lantern", "candle",
            "peaceful", "serene", "light", "spiritual"
        ]
        return any(good in tags_lower for good in whitelist)

# ═══════════════════════════════════════════════════════════
# 🌟 جلب فيديو من Pixabay (مع الفلترة)
# ═══════════════════════════════════════════════════════════
def fetch_video(query, work_dir, used_urls, video_type="film", category="romantic"):
    if not PIXABAY_KEY: return None
    try:
        r = requests.get("https://pixabay.com/api/videos/", 
                        params={"key": PIXABAY_KEY, "q": query, "video_type": video_type, 
                                "per_page": 50, "safesearch": "true"}, 
                        timeout=20)
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            random.shuffle(hits)
            for hit in hits:
                tags = hit.get("tags", "")
                
                # فلترة أمنية
                if not is_video_safe(tags, category):
                    continue
                
                url = hit.get("videos", {}).get("medium", {}).get("url")
                if url and url not in used_urls:
                    out = work_dir / f"vid_{hit['id']}.mp4"
                    with open(out, "wb") as f:
                        for chunk in requests.get(url, timeout=60, stream=True).iter_content(8192):
                            f.write(chunk)
                    used_urls.add(url)
                    print(f"   ✅ [{video_type}] {query} | tags: {tags[:40]}")
                    return str(out)
    except: pass
    return None

# ═══════════════════════════════════════════════════════════
# 🌟 خلفية ذهبية احتياطية
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
# 🌟 طبقات الأنيميشن
# ═══════════════════════════════════════════════════════════
def create_bokeh_layer():
    W, H = VIDEO_W + 400, VIDEO_H + 400
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _ in range(80):
        x, y = random.randint(0, W), random.randint(0, H)
        r = random.randint(20, 80)
        alpha = random.randint(60, 180)
        color = random.choice([(255, 215, 0), (255, 230, 150), (212, 175, 55), (255, 200, 50)])
        draw.ellipse([x-r, y-r, x+r, y+r], fill=(*color, alpha))
    return img.filter(ImageFilter.GaussianBlur(radius=10))

def create_sparkle_layer():
    W, H = VIDEO_W + 300, VIDEO_H + 300
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _ in range(150):
        x, y = random.randint(0, W), random.randint(0, H)
        r = random.randint(2, 5)
        draw.ellipse([x-r, y-r, x+r, y+r], fill=(255, 255, 220, 255))
    return img

def create_light_leak():
    img = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = random.randint(0, VIDEO_W)
    y = random.randint(0, VIDEO_H)
    r = random.randint(300, 500)
    draw.ellipse([x-r, y-r, x+r, y+r], fill=(255, 220, 150, 80))
    return img.filter(ImageFilter.GaussianBlur(radius=80))

# ═══════════════════════════════════════════════════════════
# 🌟 الاسم (بدون أسطر - خط ذهبي فخم)
# ═══════════════════════════════════════════════════════════
def create_name_overlay(name):
    if not name: return None
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    font_path = None
    for p in ["/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf",
              "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
              "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"]:
        if os.path.exists(p): font_path = p; break
    if not font_path: return None
    
    shaped = arabic_reshaper.reshape(name)
    font = ImageFont.truetype(font_path, 220, layout_engine=ImageFont.Layout.RAQM)
    
    bbox = draw.textbbox((0, 0), shaped, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = (VIDEO_W - tw) // 2, (VIDEO_H - th) // 2
    
    # ظل قوي جداً عشان يبان على أي خلفية
    draw.text((x+6, y+6), shaped, font=font, fill="black", stroke_width=12, stroke_fill="black")
    # الاسم الذهبي الفخم
    draw.text((x, y), shaped, font=font, fill="#FFD700", stroke_width=6, stroke_fill="#8B6914")
    
    return overlay

# ═══════════════════════════════════════════════════════════
# 🌟 بناء الفيديو
# ═══════════════════════════════════════════════════════════
def build_whisper_video(audio_path, json_path, out_mp4):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    category = ep.get("category", "romantic")
    name = ep.get("target_name", "")
    
    work_dir = Path(tempfile.mkdtemp(prefix="whisper_vid_"))
    
    # 1. مدة الصوت
    dur_out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path], capture_output=True, text=True).stdout.strip()
    duration = float(dur_out or 10.0)
    print(f"⏱️  مدة الصوت: {duration:.1f} ثانية")
    
    # 2. جلب المشاهد
    queries = get_queries(category)
    random.shuffle(queries)
    used_urls, scene_paths = set(), []
    
    # التوزيع: نص رسوم متحركة ونص حقيقي
    wanted_types = ["animation", "film", "animation", "film"]
    
    for v_type in wanted_types:
        filtered_queries = [q for q, t in queries if t == v_type]
        random.shuffle(filtered_queries)
        found = False
        for q in filtered_queries:
            v = fetch_video(q, work_dir, used_urls, video_type=v_type, category=category)
            if v:
                scene_paths.append((v, v_type))
                found = True
                break
        if not found:
            fb = work_dir / f"fb_{len(scene_paths)}.jpg"
            make_golden_fallback(fb)
            scene_paths.append((str(fb), "film"))
            print(f"   ⚠️ خلفية ذهبية احتياطية")
    
    scene_dur = (duration + (NUM_SCENES - 1) * CROSSFADE) / NUM_SCENES
    print(f"🎬 {NUM_SCENES} مشاهد × {scene_dur:.1f}ث + Ken Burns")
    
    # 3. معالجة المشاهد (زوم متوازن + تدرج لوني)
    processed = []
    for i, (sp, v_type) in enumerate(scene_paths):
        out = work_dir / f"scene_{i}.mp4"
        is_img = sp.endswith(('.jpg', '.jpeg', '.png'))
        input_flags = ["-loop", "1"] if is_img else ["-stream_loop", "-1"]
        
        # زوم خفيف جداً (مش هنستخدم zoompan عشان ما يكسرش الجودة)
        # هنستخدم scale ثابت مع crop، والزوم هيبقى بسيط
        vf = (
            f"scale={VIDEO_W*2}:{VIDEO_H*2}:flags=lanczos,"
            f"zoompan=z='min(zoom+0.0008,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={VIDEO_W}x{VIDEO_H}:fps=30,"
            f"colorbalance=rs=0.08:gs=0.02:bs=-0.05,"
            f"eq=brightness=0.02:contrast=1.1:saturation=1.15,"
            f"vignette=PI/5,"
            f"format=yuv420p"
        )
        cmd = ["ffmpeg", "-y"] + input_flags + [
            "-i", sp, "-t", str(scene_dur),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            str(out)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        processed.append(str(out))
    
    # 4. دمج المشاهد
    bg_video = work_dir / "bg.mp4"
    inputs = []
    for p in processed: inputs.extend(["-i", p])
    
    filters, last, offset = [], "[0:v]", 0
    for i in range(1, len(processed)):
        offset += scene_dur - CROSSFADE
        new = f"[x{i}]"
        filters.append(f"{last}[{i}:v]xfade=transition=fade:duration={CROSSFADE}:offset={offset:.3f}{new}")
        last = new
    
    subprocess.run(["ffmpeg", "-y"] + inputs + [
        "-filter_complex", ";".join(filters), "-map", last,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        str(bg_video)
    ], check=True, capture_output=True)
    
    # 5. تحضير الطبقات
    bokeh_path = work_dir / "bokeh.png"
    create_bokeh_layer().save(bokeh_path)
    sparkle_path = work_dir / "sparkle.png"
    create_sparkle_layer().save(sparkle_path)
    leak_path = work_dir / "leak.png"
    create_light_leak().save(leak_path)
    
    name_path = None
    name_img = create_name_overlay(name)
    if name_img:
        name_path = work_dir / "name.png"
        name_img.save(name_path)
    
    # 6. دمج الطبقات
    inputs = ["-i", str(bg_video)]
    idx = 1
    
    inputs.extend(["-loop", "1", "-i", str(bokeh_path)]); idx_bokeh = idx; idx += 1
    inputs.extend(["-loop", "1", "-i", str(sparkle_path)]); idx_sparkle = idx; idx += 1
    inputs.extend(["-loop", "1", "-i", str(leak_path)]); idx_leak = idx; idx += 1
    
    idx_name = None
    if name_path:
        inputs.extend(["-loop", "1", "-i", str(name_path)]); idx_name = idx; idx += 1
    
    filters = []
    filters.append(f"[{idx_bokeh}:v]format=rgba,colorchannelmixer=aa=0.3[bk]")
    filters.append(f"[{idx_sparkle}:v]format=rgba,colorchannelmixer=aa=0.5[sp]")
    filters.append(f"[{idx_leak}:v]format=rgba,colorchannelmixer=aa=0.4,fade=in:st=0:d=1:alpha=1[leak]")
    if name_path:
        filters.append(f"[{idx_name}:v]format=rgba,fade=in:st=0.5:d=1.5:alpha=1[name]")
    
    filters.append(f"[0:v][bk]overlay=x='-100+50*sin(t/8)':y='-100+50*cos(t/10)':shortest=1[v1]")
    filters.append(f"[v1][sp]overlay=x='-50+30*cos(t/6)':y='-50+30*sin(t/9)':shortest=1[v2]")
    filters.append(f"[v2][leak]overlay=0:0:shortest=1[v3]")
    
    if name_path:
        filters.append(f"[v3][name]overlay=0:0:shortest=1,format=yuv420p[vout]")
    else:
        filters.append(f"[v3]format=yuv420p[vout]")
    
    final_vid = work_dir / "final_vid.mp4"
    subprocess.run(["ffmpeg", "-y"] + inputs + [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-t", str(duration),
        "-c:v", "libx264", "-preset", "faster", "-crf", "28",
        str(final_vid)
    ], check=True, capture_output=True)
    
    # 7. دمج الصوت + الضغط الذكي
    print("💾 ضغط الفيديو (Smart Compression)...")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final_vid), "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "slow", "-crf", "28",
        "-maxrate", "1.5M", "-bufsize", "3M",
        "-profile:v", "main", "-level", "4.0",
        "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "96k",
        "-t", str(duration), str(out_mp4)
    ], check=True, capture_output=True)
    
    size_mb = os.path.getsize(out_mp4) / (1024 * 1024)
    print(f"✅ فيديو: {out_mp4} | {size_mb:.2f} MB | مشاهد: {len(processed)} | الاسم: {name or 'عام'}")
    cleanup_dir(work_dir)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(1)
    build_whisper_video(sys.argv[1], sys.argv[2], sys.argv[3])
