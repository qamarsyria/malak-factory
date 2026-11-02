import os
import requests
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = _REPO_ROOT / "assets" / "audio"
MUSIC_DIR = AUDIO_DIR / "music"
SFX_DIR = AUDIO_DIR / "sfx"

ASSETS = {
    # موسيقى القصص
    "music/calm.mp3": "https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3",
    "music/sad.mp3": "https://cdn.pixabay.com/download/audio/2022/03/10/audio_2c3a9f0e4e.mp3",
    "music/epic.mp3": "https://cdn.pixabay.com/download/audio/2022/01/18/audio_d0a13f69d2.mp3",
    "music/battle.mp3": "https://cdn.pixabay.com/download/audio/2021/11/25/audio_00fa5593f3.mp3",
    "music/conspiracy.mp3": "https://cdn.pixabay.com/download/audio/2022/03/15/audio_c8c8a73467.mp3",
    # موسيقى الهمسات
    "music/romantic.mp3": "https://cdn.pixabay.com/download/audio/2022/02/22/audio_d1718ab41b.mp3",
    
    # مؤثرات صوتية
    "sfx/rain.mp3": "https://cdn.pixabay.com/download/audio/2022/07/04/audio_2c3a9f0e4e.mp3",
    "sfx/thunder.mp3": "https://cdn.pixabay.com/download/audio/2022/03/10/audio_2c3a9f0e4e.mp3",
    "sfx/wind.mp3": "https://cdn.pixabay.com/download/audio/2022/03/15/audio_c8c8a73467.mp3",
    "sfx/birds.mp3": "https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3",
    "sfx/sword.mp3": "https://cdn.pixabay.com/download/audio/2021/11/25/audio_00fa5593f3.mp3",
    "sfx/heartbeat.mp3": "https://cdn.pixabay.com/download/audio/2022/01/18/audio_d0a13f69d2.mp3",
    "sfx/whisper.mp3": "https://cdn.pixabay.com/download/audio/2022/02/22/audio_d1718ab41b.mp3",
}

def ensure_assets():
    """يتأكد من وجود جميع الملفات الصوتية، ويحملها إن لم تكن موجودة."""
    print("🎵 فحص المجلدات الصوتية...")
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    
    for rel_path, url in ASSETS.items():
        target = AUDIO_DIR / rel_path
        if target.exists() and target.stat().st_size > 1000:
            continue
            
        print(f"⬇️ تحميل: {rel_path}")
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                target.write_bytes(r.content)
        except Exception as e:
            print(f"⚠️ فشل تحميل {rel_path}: {e}")
            
    # التحقق من وجود "يا طيبة" في المكان الذي رفعتها فيه
    ya_taybah = AUDIO_DIR / "ya_taybah.mp3"
    if ya_taybah.exists():
        print("✅ تم العثور على أنشودة 'يا طيبة'")
    else:
        print("ℹ️ لم يتم العثور على 'يا طيبة'. إذا أردت استخدامها، ارفعها باسم ya_taybah.mp3 في مجلد assets/audio")
