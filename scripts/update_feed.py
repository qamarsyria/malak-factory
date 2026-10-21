import os
import json
import base64
import requests

def add_video_to_feed(title, description, video_url, category="عام", views=100, likes=50, shares=10):
    REPO = "qamarsyria/qamar-malak" # مستودع المنصة
    FILE_PATH = "videos.json"
    GITHUB_TOKEN = os.environ.get("GH_PAT")

    if not GITHUB_TOKEN:
        print("❌ خطأ: GH_PAT غير موجود")
        return False

    url = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            content = base64.b64decode(r.json()['content']).decode('utf-8')
            videos = json.loads(content)
            sha = r.json()['sha']
        else:
            videos = []
            sha = None

        new_video = {
            "title": title,
            "description": description,
            "video_url": video_url,
            "category": category,
            "views": views,
            "likes": likes,
            "shares": shares
        }
        
        if any(v.get("video_url") == video_url for v in videos):
            print("⚠️ الفيديو موجود مسبقاً.")
            return True

        videos.insert(0, new_video)
        new_content = base64.b64encode(json.dumps(videos, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
        
        data = {
            "message": f"🎬 إضافة فيديو جديد: {title}",
            "content": new_content
        }
        if sha:
            data["sha"] = sha

        r_put = requests.put(url, headers=headers, json=data)
        
        if r_put.status_code in [200, 201]:
            print(f"✅ تمت إضافة الفيديو للمنصة: {title}")
            return True
        else:
            print(f"❌ فشل الرفع: {r_put.status_code} - {r_put.text}")
            return False

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False
