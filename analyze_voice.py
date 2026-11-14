import librosa
import numpy as np
import parselmouth
from parselmouth.praat import call
import json
import warnings
warnings.filterwarnings("ignore")

def extract_features(audio_path):
    y, sr = librosa.load(audio_path, sr=16000)
    y, _ = librosa.effects.trim(y, top_db=30)
    
    # 1. Pitch (F0)
    f0, _, _ = librosa.pyin(y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
    f0 = f0[~np.isnan(f0)]
    pitch_median = float(np.median(f0)) if len(f0) > 0 else 0.0
    pitch_std = float(np.std(f0)) if len(f0) > 0 else 0.0
    
    # 2. Formants (F1, F2, F3)
    sound = parselmouth.Sound(audio_path)
    formant = sound.to_formant_burg()
    times = formant.ts()
    f1_list, f2_list, f3_list = [], [], []
    for t in times:
        if t % 0.1 < 0.02:
            f1_list.append(formant.get_value_at_time(1, t))
            f2_list.append(formant.get_value_at_time(2, t))
            f3_list.append(formant.get_value_at_time(3, t))
    
    f1_med = float(np.nanmedian(f1_list)) if f1_list else 0.0
    f2_med = float(np.nanmedian(f2_list)) if f2_list else 0.0
    f3_med = float(np.nanmedian(f3_list)) if f3_list else 0.0
    
    # 3. MFCC (طابع الصوت)
    mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mfcc_mean = np.mean(mfccs, axis=1).tolist()
    
    return {
        "pitch_median": pitch_median,
        "pitch_std": pitch_std,
        "formants": {"F1": f1_med, "F2": f2_med, "F3": f3_med},
        "mfcc": mfcc_mean
    }

if __name__ == "__main__":
    user_features = extract_features("my_voice.wav")
    with open("user_features.json", "w") as f:
        json.dump(user_features, f, indent=4)
    
    print("✅ تحليل صوتك:")
    print(f"   - التردد الأساسي: {user_features['pitch_median']:.1f} Hz")
    print(f"   - الفورمانت (F1, F2, F3): {user_features['formants']}")
