import os
import json
import urllib.request
import subprocess
import numpy as np
from analyze_voice import extract_features

RECITERS = {
    "Alafasy": "Alafasy_128kbps",
    "Abdul_Basit": "Abdul_Basit_Murattal_192kbps",
    "Minshawy": "Minshawy_Murattal_128kbps",
    "Sudais": "Abdurrahmaan_As-Sudais_192kbps",
    "Husary": "Husary_128kbps"
}

def download_and_analyze(name, folder):
    url = f"https://everyayah.com/data/{folder}/002255.mp3"
    out = f"tmp_{name}.mp3"
    wav = f"tmp_{name}.wav"
    
    urllib.request.urlretrieve(url, out)
    subprocess.run(["ffmpeg", "-y", "-i", out, "-ar", "16000", "-ac", "1", wav], capture_output=True)
    
    features = extract_features(wav)
    os.remove(out)
    os.remove(wav)
    return features

def distance(f1, f2):
    # حساب المسافة بين الترددات والفورمانت
    pitch_diff = abs(f1["pitch_median"] - f2["pitch_median"]) / 100.0
    f1_diff = abs(f1["formants"]["F1"] - f2["formants"]["F1"]) / 100.0
    f2_diff = abs(f1["formants"]["F2"] - f2["formants"]["F2"]) / 100.0
    
    # حساب تشابه MFCC (بصمة الصوت)
    mfcc1 = np.array(f1["mfcc"])
    mfcc2 = np.array(f2["mfcc"])
    mfcc_diff = np.linalg.norm(mfcc1 - mfcc2) / 100.0
    
    return pitch_diff * 2 + f1_diff + f2_diff + mfcc_diff

if __name__ == "__main__":
    with open("user_features.json", "r") as f:
        user = json.load(f)
    
    print("⏳ جاري تحليل القراء ومطابقتهم مع صوتك...")
    results = []
    
    for name, folder in RECITERS.items():
        print(f"   - تحليل: {name}")
        reciter_features = download_and_analyze(name, folder)
        score = distance(user, reciter_features)
        results.append({
            "name": name,
            "folder": folder,
            "distance": round(score, 3),
            "pitch": round(reciter_features["pitch_median"], 1)
        })
    
    results.sort(key=lambda x: x["distance"])
    
    print("\n🏆 الترتيب (الأقرب لصوتك أولاً):")
    for i, r in enumerate(results):
        print(f"   {i+1}. {r['name']} | المسافة: {r['distance']} | التردد: {r['pitch']} Hz")
    
    with open("best_match.json", "w") as f:
        json.dump(results[0], f)
    
    print(f"\n✅ القارئ الأقرب لصوتك: {results[0]['name']}")
