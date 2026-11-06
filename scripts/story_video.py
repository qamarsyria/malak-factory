# ابحث عن دالة fetch_media واستبدلها بهذا الكود
def fetch_media(query, work_dir, used_urls):
    local = _find_local(query)
    if local: return local
    
    if not PIXABAY_KEY:
        return ""

    try:
        r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_KEY, "q": query, "video_type": "film", "safesearch": "true", "per_page": 30, "min_width": 1080}, timeout=20)
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            random.shuffle(hits)
            for hit in hits:
                tags = hit.get("tags", "").lower()
                
                # 🌟 فلترة صارمة: إذا لم يكن البحث عن niqab، نرفض أي فيديو يحتوي على نساء أو بشر
                if "niqab" not in query.lower():
                    # قائمة سوداء شاملة
                    forbidden = ["woman", "women", "girl", "girls", "female", "lady", "man", "men", "person", "people", "human", "child", "boy", "kid", "baby", "face", "crowd"]
                    if any(bad in tags for bad in forbidden):
                        continue
                
                url = hit.get("videos", {}).get("large", {}).get("url") or hit.get("videos", {}).get("medium", {}).get("url")
                if not url or url in used_urls: continue
                
                slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
                out = work_dir / f"vid_{slug}_{hit['id']}.mp4"
                try:
                    with open(out, "wb") as f:
                        for chunk in requests.get(url, timeout=60, stream=True).iter_content(8192): f.write(chunk)
                    used_urls.add(url)
                    return str(out)
                except: pass
    except Exception as e:
        print(f"⚠️ خطأ في جلب الفيديو: {e}")
        
    return ""
