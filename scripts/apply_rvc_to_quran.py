"""
quran_voice_studio.py — استوديو خاص شامل:
1) وضع القرآن: اختيار القارئ + اسم السورة أو رقمها + الآيات.
2) وضع النص اليدوي: تكتب النص بدون تشكيل -> تشكيل وإعراب وتصريف تلقائي مزدوج -> سرد Gemini -> البصمات + الصدى.
3) معالجة بصمة الصوت الرجالي (my_voice) بنفس هندسة النقاء والوضوح (atempo=0.75 + EQ الوضوح + Compressor + منع خلط -6/-4 المتعارض)،
   مع خيار مقارنة my_voice_air (مزج هواء مخارج القارئ الصامتة > 4200Hz) في وضع compare_all.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from story_audio import smart_diacritize, arabize_numbers, split_text, _merge_diacritics_safely
from gemini_tts_pool import gemini_tts_with_pool

OUT_DIR = _REPO_ROOT / "out" / "studio"
WORK_DIR = _REPO_ROOT / "temp_studio_work"
RVC_DIR = _REPO_ROOT / "rvc"

QURAN_BASE_URL = "https://everyayah.com/data"
TARGET_SR = "48000"

RECITER_NAMES = {
    "Husary_128kbps":                 "الحصري (مرتل)",
    "Husary_128kbps_Mujawwad":        "الحصري (مجوّد)",
    "Alafasy_128kbps":                "مشاري العفاسي",
    "Abdurrahmaan_As-Sudais_192kbps": "عبد الرحمن السديس",
    "Ghamadi_40kbps":                 "سعد الغامدي",
    "Yasser_Ad-Dussary_128kbps":      "ياسر الدوسري",
    "Nasser_Alqatami_128kbps":        "ناصر القطامي",
    "MaherAlMuaiqly128kbps":          "ماهر المعيقلي",
    "Minshawy_Murattal_128kbps":      "المنشاوي (مرتل)",
    "Minshawy_Mujawwad_192kbps":      "المنشاوي (مجوّد)",
    "Abdul_Basit_Murattal_192kbps":   "عبد الباسط (مرتل)",
}

SURAH_FALLBACK = [
    (1, "الفاتحة", 7), (2, "البقرة", 286), (3, "آل عمران", 200), (4, "النساء", 176),
    (5, "المائدة", 120), (6, "الأنعام", 165), (7, "الأعراف", 206), (8, "الأنفال", 75),
    (9, "التوبة", 129), (10, "يونس", 109), (11, "هود", 123), (12, "يوسف", 111),
    (13, "الرعد", 43), (14, "إبراهيم", 52), (15, "الحجر", 99), (16, "النحل", 128),
    (17, "الإسراء", 111), (18, "الكهف", 110), (19, "مريم", 98), (20, "طه", 135),
    (21, "الأنبياء", 112), (22, "الحج", 78), (23, "المؤمنون", 118), (24, "النور", 64),
    (25, "الفرقان", 77), (26, "الشعراء", 227), (27, "النمل", 93), (28, "القصص", 88),
    (29, "العنكبوت", 69), (30, "الروم", 60), (31, "لقمان", 34), (32, "السجدة", 30),
    (33, "الأحزاب", 73), (34, "سبأ", 54), (35, "فاطر", 45), (36, "يس", 83),
    (37, "الصافات", 182), (38, "ص", 88), (39, "الزمر", 75), (40, "غافر", 85),
    (41, "فصلت", 54), (42, "الشورى", 53), (43, "الزخرف", 89), (44, "الدخان", 59),
    (45, "الجاثية", 37), (46, "الأحقاف", 35), (47, "محمد", 38), (48, "الفتح", 29),
    (49, "الحجرات", 18), (50, "ق", 45), (51, "الذاريات", 60), (52, "الطور", 49),
    (53, "النجم", 62), (54, "القمر", 55), (55, "الرحمن", 78), (56, "الواقعة", 96),
    (57, "الحديد", 29), (58, "المجادلة", 22), (59, "الحشر", 24), (60, "الممتحنة", 13),
    (61, "الصف", 14), (62, "الجمعة", 11), (63, "المنافقون", 11), (64, "التغابن", 18),
    (65, "الطلاق", 12), (66, "التحريم", 12), (67, "الملك", 30), (68, "القلم", 52),
    (69, "الحاقة", 52), (70, "المعارج", 44), (71, "نوح", 28), (72, "الجن", 28),
    (73, "المزمل", 20), (74, "المدثر", 56), (75, "القيامة", 40), (76, "الإنسان", 31),
    (77, "المرسلات", 50), (78, "النبأ", 40), (79, "النازعات", 46), (80, "عبس", 42),
    (81, "التكوير", 29), (82, "الانفطار", 19), (83, "المطففين", 36), (84, "الانشقاق", 25),
    (85, "البروج", 22), (86, "الطارق", 17), (87, "الأعلى", 19), (88, "الغاشية", 26),
    (89, "الفجر", 30), (90, "البلد", 20), (91, "الشمس", 15), (92, "الليل", 21),
    (93, "الضحى", 11), (94, "الشرح", 8), (95, "التين", 8), (96, "العلق", 19),
    (97, "القدر", 5), (98, "البينة", 8), (99, "الزلزلة", 8), (100, "العاديات", 11),
    (101, "القارعة", 11), (102, "التكاثر", 8), (103, "العصر", 3), (104, "الهمزة", 9),
    (105, "الفيل", 5), (106, "قريش", 4), (107, "الماعون", 7), (108, "الكوثر", 3),
    (109, "الكافرون", 6), (110, "النصر", 3), (111, "المسد", 5), (112, "الإخلاص", 4),
    (113, "الفلق", 5), (114, "الناس", 6),
]


def _norm_ar(s: str) -> str:
    s = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", s or "").strip()
    s = s.replace("سورة", "").strip()
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    s = s.replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", "", s)


def resolve_surah(query: str) -> tuple[int, str, int]:
    q = (query or "").strip()
    if q.isdigit():
        num = int(q)
        for s_num, s_name, max_a in SURAH_FALLBACK:
            if s_num == num:
                return s_num, s_name, max_a
        raise ValueError(f"❌ رقم سورة غير صحيح: {q}")

    nq = _norm_ar(q)
    for s_num, s_name, max_a in SURAH_FALLBACK:
        if _norm_ar(s_name) == nq:
            return s_num, s_name, max_a
    for s_num, s_name, max_a in SURAH_FALLBACK:
        if nq in _norm_ar(s_name):
            return s_num, s_name, max_a
    raise ValueError(f"❌ لم يتم التعرف على اسم السورة: {query}")


def parse_ayahs(spec: str, max_ayah: int) -> list[int]:
    spec = (spec or "1").strip()
    nums = []
    for part in re.split(r"[،,;]+", spec):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a.strip()), int(b.strip())
            if start > end:
                start, end = end, start
            for x in range(start, end + 1):
                if 1 <= x <= max_ayah and x not in nums:
                    nums.append(x)
        elif part.isdigit():
            x = int(part)
            if 1 <= x <= max_ayah and x not in nums:
                nums.append(x)
    if not nums:
        raise ValueError(f"❌ صيغة الآيات غير صالحة أو خارج نطاق السورة (1..{max_ayah}): {spec}")
    return nums


def get_dur(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def download_ayah(surah: int, ayah: int, reciter_id: str, out_wav: Path):
    mp3_path = out_wav.with_suffix(".mp3")
    url = f"{QURAN_BASE_URL}/{reciter_id}/{surah:03d}{ayah:03d}.mp3"
    r = requests.get(url, timeout=30)
    if r.status_code != 200 or len(r.content) < 1000:
        raise RuntimeError(f"❌ فشل تحميل الآية {surah}:{ayah} من {reciter_id} (HTTP {r.status_code})")
    mp3_path.write_bytes(r.content)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3_path), "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
        check=True, capture_output=True,
    )


def clean_manual_text(text: str) -> str:
    """ينظف النص اليدوي مع الإبقاء التام على أي كلمات داخل الأقواس () أو ﴿﴾."""
    if not text:
        return ""
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    text = text.replace("ﷺ", " صلى الله عليه وسلم ")
    text = text.replace("صلى الله عليه وسلم", " صلى الله عليه وسلم ")
    text = re.sub(r"[()\[\]﴿﴾«»\"'`]", " ", text)
    text = re.sub(r"[*#@_•/\\|~=+]", " ", text)
    text = arabize_numbers(text)
    text = re.sub(r"[^\u0621-\u064A\u064B-\u065F\u0670\s،؛؟!.:]", " ", text)
    text = re.sub(r"\s*([،؛؟!.:])\s*", r"\1 ", text)
    return re.sub(r"\s+", " ", text).strip()


def _diacritize_ratio(text: str) -> float:
    letters = max(1, len(re.findall(r"[\u0621-\u064A]", text)))
    diacs = len(re.findall(r"[\u064B-\u0652]", text))
    return diacs / letters


def _diacritize_via_github_models(text: str) -> str:
    gh_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not gh_token:
        return ""
    prompt = (
        "أنت أستاذ لغوي متخصص في النحو والصرف العربي والتشكيل الصوتي الكامل.\n"
        "اشكل النص التالي تشكيلاً كاملاً ودقيقاً 100% (الفتحة والضمة والكسرة والتنوين والشدّة على الحرف المشدد واللام الشمسية والسكون في الوقف)، "
        "مع التمييز الدقيق بين المبني للمعلوم والمجهول وإعراب أواخر الكلمات دون تغيير أو حذف أي كلمة.\n\n"
        f"النص:\n{text}\n\n"
        'أعد JSON فقط بهذا الشكل: {"diacritized": "النص مشكولاً بالكامل"}'
    )
    for model in ("openai/gpt-4.1", "openai/gpt-4o", "openai/gpt-4o-mini"):
        try:
            r = requests.post(
                "https://models.github.ai/inference/chat/completions",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {gh_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": 0.05,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            if r.status_code == 200:
                txt = r.json()["choices"][0]["message"]["content"]
                s, e = txt.find("{"), txt.rfind("}")
                if s != -1 and e > s:
                    out = json.loads(txt[s : e + 1]).get("diacritized", "").strip()
                    if out:
                        return _merge_diacritics_safely(text, out)
        except Exception:
            continue
    return ""


def diacritize_studio_text(clean_para: str) -> str:
    sub_parts = split_text(clean_para, max_chars=500)
    diac_parts = []
    for part in sub_parts:
        res = smart_diacritize(part)
        ratio = _diacritize_ratio(res)
        if ratio < 0.35:
            print(f"   ⚠️ نسبة التشكيل الأولية منخفضة ({ratio:.2f}) — جاري التعزيز عبر GitHub Models...", flush=True)
            gh_res = _diacritize_via_github_models(part)
            if gh_res and _diacritize_ratio(gh_res) > ratio:
                res = gh_res
                ratio = _diacritize_ratio(res)
        print(f"   ✅ نسبة التشكيل النهائية: {ratio:.2f} حركة/حرف", flush=True)
        diac_parts.append(res)
    return " ".join(diac_parts).strip()


def prepare_custom_text_units(raw_input: str, do_diacritize: bool, gemini_voice: str) -> tuple[list[dict], str]:
    txt = (raw_input or "").strip()
    if txt.startswith("@file:"):
        fpath = _REPO_ROOT / txt[6:].strip()
        if not fpath.exists():
            raise RuntimeError(f"❌ الملف غير موجود: {fpath}")
        txt = fpath.read_text(encoding="utf-8").strip()

    if not txt:
        raise RuntimeError("❌ خانة النص اليدوي فارغة! اكتب نصاً أو استخدم @file:data/studio_text.txt")

    txt = txt.replace("|", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n+", txt) if p.strip()]

    diac_paragraphs = []
    units = []
    unit_idx = 0

    for p_idx, para in enumerate(paragraphs):
        clean_para = clean_manual_text(para)
        if not clean_para:
            continue

        if do_diacritize:
            print(f"🔤 جاري التشكيل والإعراب التلقائي للفقرة {p_idx + 1}...", flush=True)
            diac_para = diacritize_studio_text(clean_para)
        else:
            diac_para = clean_para

        diac_paragraphs.append(diac_para)
        chunks = split_text(diac_para, max_chars=350)

        for c in chunks:
            if not c.strip() or not re.search(r"[\u0621-\u064A]", c):
                continue
            unit_idx += 1
            raw_mp3 = WORK_DIR / f"gem_raw_{unit_idx:03d}.mp3"
            raw_wav = WORK_DIR / f"gem_raw_{unit_idx:03d}.wav"
            print(f"🎙️ سرد Gemini ({gemini_voice}) للجزء #{unit_idx}: {c[:60]}...", flush=True)
            gemini_tts_with_pool(c, str(raw_mp3), voice=gemini_voice)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(raw_mp3),
                    "-af", (
                        "silenceremove=start_periods=1:start_duration=0:start_threshold=-48dB,"
                        "areverse,"
                        "silenceremove=start_periods=1:start_duration=0:start_threshold=-48dB,"
                        "areverse"
                    ),
                    "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(raw_wav),
                ],
                check=True, capture_output=True,
            )
            units.append({
                "key": f"txt_{unit_idx:03d}",
                "wav": raw_wav,
                "text": c,
                "is_quran": False,
            })
            time.sleep(0.4)

    if not units:
        raise RuntimeError("❌ لم ينتج عن النص اليدوي أي مقاطع صوتية صالحة.")

    full_diac_text = "\n".join(diac_paragraphs)
    print("\n" + "─" * 50)
    print("📜 النص النهائي بعد التشكيل:")
    print(full_diac_text)
    print("─" * 50 + "\n", flush=True)
    return units, full_diac_text


def run_rvc_cli(in_wav: Path, out_wav: Path, model_rel: str, index_rel: str, pitch: int, protect: float, index_rate: float, rms_mix: float):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(RVC_DIR)
    env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

    if out_wav.exists():
        out_wav.unlink()

    cmd = [
        sys.executable, "infer/cli.py",
        "--model", model_rel,
        "--input", str(in_wav.resolve()),
        "--output", str(out_wav.resolve()),
        "--index", index_rel,
        "--f0-method", "rmvpe",
        "--pitch", str(pitch),
        "--protect", str(protect),
        "--index-rate", str(index_rate),
        "--rms-mix-rate", str(rms_mix),
    ]
    res = subprocess.run(cmd, cwd=str(RVC_DIR), env=env, capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size < 1000:
        raise RuntimeError(f"❌ فشل RVC ({model_rel}): {res.stderr[-500:]}")


def _parse_my_pitch(raw_spec: str, audio_for_f0: Path) -> int:
    s = (raw_spec or "0").strip().lower()
    if s == "auto":
        auto_pitch = -2
        try:
            import librosa
            import numpy as np
            y, _sr = librosa.load(str(audio_for_f0), sr=None)
            f0, voiced_flag, _ = librosa.pyin(y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"))
            f0_voiced = f0[voiced_flag]
            if len(f0_voiced) > 0:
                input_hz = float(np.median(f0_voiced))
                auto_pitch = round(12 * math.log2(125.0 / input_hz))
                print(f"   🎯 تحليل طبقة الصوت لبصمتي: {input_hz:.1f} Hz | Auto={auto_pitch}", flush=True)
        except Exception as e:
            print(f"   ⚠️ تعذر التحليل التلقائي لطبقة بصمتي ({e}) — استخدام -2", flush=True)
        return max(-7, min(5, auto_pitch))

    val = int(re.sub(r"[^0-9\-+]", "", s))
    return max(-12, min(12, val))


def build_my_voice_dry(in_48k: Path, out_dry: Path, pitch_spec: str, use_air_blend: bool = False):
    clean_w = RVC_DIR / "assets/weights/clean.pth"
    if not clean_w.exists():
        raise RuntimeError("❌ ملف بصمتي clean.pth غير موجود")

    idx_arg = "clean.index" if (RVC_DIR / "clean.index").exists() else ""
    pre_cleaned = out_dry.with_name(out_dry.stem + "_pre.wav")
    rvc_raw = out_dry.with_name(out_dry.stem + "_raw.wav")

    # معالجة التبطيء 0.75 في الوضع الصافي تعطي RVC وضوحاً مضاعفاً؛ أما في وضع air_blend نبقي التوقيت 1:1 للتطابق التام
    use_tempo_trick = not use_air_blend
    tempo_slow = 0.75
    tempo_fast = 1.0 / tempo_slow
    pre_af = (
        f"highpass=f=60,lowpass=f=15500,afftdn=nr=2:nf=-50,atempo={tempo_slow}"
        if use_tempo_trick
        else "highpass=f=60,lowpass=f=15500,afftdn=nr=2:nf=-50"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(in_48k), "-af", pre_af,
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(pre_cleaned)],
        check=True, capture_output=True,
    )

    final_pitch = _parse_my_pitch(pitch_spec, pre_cleaned)
    print(f"   🎛️ باتش بصمتي الفعلي: {final_pitch} | tempo_trick={use_tempo_trick} | air_blend={use_air_blend}", flush=True)

    # معاملات متوازنة تبرز مخارج الحروف وبصمتك معاً، دون خلط rough المتضارب
    run_rvc_cli(pre_cleaned, rvc_raw, "assets/weights/clean.pth", idx_arg, final_pitch, 0.38, 0.80, 0.25)

    clarity_chain = (
        (f"atempo={tempo_fast}," if use_tempo_trick else "")
        + "highpass=f=75,lowpass=f=14500,afftdn=nf=-30,"
        "equalizer=f=240:t=q:w=1.5:g=-3.0,"
        "equalizer=f=1500:t=q:w=1.5:g=2.0,"
        "equalizer=f=3200:t=q:w=1.3:g=4.5,"
        "equalizer=f=5500:t=q:w=1.2:g=3.0,"
        "acompressor=threshold=0.089:ratio=3:attack=15:release=200"
    )

    if use_air_blend:
        fc = (
            f"[0:a]{clarity_chain}[v0];"
            "[1:a]highpass=f=4200,volume=0.22[air];"
            "[v0][air]amix=inputs=2:duration=first:normalize=0,"
            "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(rvc_raw), "-i", str(in_48k),
             "-filter_complex", fc, "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_dry)],
            check=True, capture_output=True,
        )
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(rvc_raw),
             "-af", f"{clarity_chain},loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95",
             "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_dry)],
            check=True, capture_output=True,
        )


def _parse_malak_pitch(raw_spec: str, pre_cleaned: Path) -> int:
    s = (raw_spec or "0").strip()
    if s.startswith("=") or s.lower().startswith("fixed:"):
        val = int(re.sub(r"[^0-9\-+]", "", s))
        return max(-12, min(14, val))

    offset = int(s)
    auto_pitch = 4
    try:
        import librosa
        import numpy as np
        y, _sr = librosa.load(str(pre_cleaned), sr=None)
        f0, voiced_flag, _ = librosa.pyin(y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"))
        f0_voiced = f0[voiced_flag]
        if len(f0_voiced) > 0:
            input_hz = float(np.median(f0_voiced))
            auto_pitch = round(12 * math.log2(190.0 / input_hz))
            print(f"   🎯 تحليل طبقة الصوت لملك: {input_hz:.1f} Hz | Auto={auto_pitch}", flush=True)
    except Exception as e:
        print(f"   ⚠️ تعذر التحليل التلقائي لطبقة ملك ({e}) — استخدام الافتراضي 4", flush=True)

    final_pitch = max(-4, min(14, auto_pitch + offset))
    print(f"   🔥 باتش ملك النهائي: {final_pitch} (offset={offset:+d})", flush=True)
    return final_pitch


def build_malak_dry(in_48k: Path, out_dry: Path, pitch_spec: str):
    malak_w = RVC_DIR / "assets/weights/malak.pth"
    if not malak_w.exists():
        raise RuntimeError("❌ ملف بصمة ملك (malak_voice.pth) غير موجود في المستودع ولا في Releases!")

    tempo_slow = 0.75
    tempo_fast = 1.0 / tempo_slow
    pre_cleaned = out_dry.with_name(out_dry.stem + "_pre.wav")
    rvc_raw = out_dry.with_name(out_dry.stem + "_raw.wav")

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(in_48k),
         "-af", f"highpass=f=60,lowpass=f=15500,afftdn=nr=2:nf=-50,atempo={tempo_slow}",
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(pre_cleaned)],
        check=True, capture_output=True,
    )

    final_pitch = _parse_malak_pitch(pitch_spec, pre_cleaned)
    idx_arg = "malak.index" if (RVC_DIR / "malak.index").exists() else ""
    run_rvc_cli(pre_cleaned, rvc_raw, "assets/weights/malak.pth", idx_arg, final_pitch, 0.5, 0.8, 0.25)

    post_filters = (
        f"atempo={tempo_fast},"
        "highpass=f=90,lowpass=f=14000,afftdn=nf=-30,"
        "equalizer=f=350:t=q:w=2:g=-3,"
        "equalizer=f=1200:t=q:w=2:g=2.5,"
        "equalizer=f=2500:t=q:w=2:g=2,"
        "equalizer=f=4500:t=q:w=2:g=-2,"
        "equalizer=f=8000:t=q:w=3:g=-3,"
        "acompressor=threshold=0.089:ratio=3:attack=20:release=250,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,"
        "alimiter=limit=0.95"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(rvc_raw), "-af", post_filters,
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_dry)],
        check=True, capture_output=True,
    )


def mix_voices(my_dry: Path | None, malak_dry: Path | None, my_pct: float, malak_pct: float, out_dry: Path):
    my_gain = max(0.0, my_pct / 100.0)
    malak_gain = max(0.0, malak_pct / 100.0)

    if my_dry and malak_dry and my_gain > 0 and malak_gain > 0:
        fc = (
            f"[0:a]volume={my_gain:.3f}[v0];"
            f"[1:a]volume={malak_gain:.3f}[v1];"
            "[v0][v1]amix=inputs=2:duration=longest:normalize=0,"
            "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(my_dry), "-i", str(malak_dry),
             "-filter_complex", fc, "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_dry)],
            check=True, capture_output=True,
        )
    elif my_dry and my_gain > 0:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(my_dry),
             "-af", f"volume={my_gain:.3f},loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95",
             "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_dry)],
            check=True, capture_output=True,
        )
    elif malak_dry and malak_gain > 0:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(malak_dry),
             "-af", f"volume={malak_gain:.3f},loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95",
             "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_dry)],
            check=True, capture_output=True,
        )
    else:
        raise RuntimeError("❌ نسبة الصوت للبصمتين صفر — اختر نسبة أعلى من 0 لإحدى البصمتين.")


def apply_custom_echo(
    in_dry: Path,
    out_wav: Path,
    delay_ms: float,
    repeats: int,
    decay: float,
    wet_pct: float,
    tail_fade_sec: float,
):
    dry_dur = get_dur(in_dry)
    repeats = max(0, min(8, int(repeats)))
    delay_ms = max(0.0, min(2000.0, float(delay_ms)))
    decay = max(0.05, min(0.92, float(decay)))
    wet_ratio = max(0.0, min(1.0, float(wet_pct) / 100.0))
    tail_fade_sec = max(0.2, min(5.0, float(tail_fade_sec)))

    if repeats == 0 or wet_ratio == 0.0 or delay_ms == 0.0:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(in_dry),
             "-af", "loudnorm=I=-15:TP=-1.5:LRA=10,alimiter=limit=0.95",
             "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
            check=True, capture_output=True,
        )
        return

    delays = [str(max(10, int(round(delay_ms * i)))) for i in range(1, repeats + 1)]
    decays = [str(max(0.005, min(0.95, round(wet_ratio * (decay ** (i - 1)), 4)))) for i in range(1, repeats + 1)]

    reverb_span_sec = (delay_ms * repeats) / 1000.0 + 0.45
    pad_sec = max(tail_fade_sec + 0.1, reverb_span_sec)

    echoed_tmp = out_wav.with_name(out_wav.stem + "_echotmp.wav")
    af_echo = (
        f"apad=pad_dur={pad_sec:.3f},"
        f"aecho=1.0:0.95:{'|'.join(delays)}:{'|'.join(decays)},"
        "loudnorm=I=-15:TP=-1.5:LRA=10,"
        "alimiter=limit=0.95"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(in_dry), "-af", af_echo,
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(echoed_tmp)],
        check=True, capture_output=True,
    )

    total_dur = get_dur(echoed_tmp)
    fade_start = max(dry_dur, total_dur - tail_fade_sec)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(echoed_tmp),
         "-af", f"afade=t=out:st={fade_start:.3f}:d={tail_fade_sec:.3f},afade=t=in:st=0:d=0.015",
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
        check=True, capture_output=True,
    )
    echoed_tmp.unlink(missing_ok=True)


def main():
    source_mode = os.getenv("STUDIO_SOURCE_MODE", "quran").strip()
    custom_text = os.getenv("STUDIO_CUSTOM_TEXT", "")
    gemini_voice = os.getenv("STUDIO_GEMINI_VOICE", "Charon").strip()

    reciter_id = os.getenv("STUDIO_RECITER", "Husary_128kbps").strip()
    surah_input = os.getenv("STUDIO_SURAH", "الكهف").strip()
    ayahs_input = os.getenv("STUDIO_AYAHS", "1-5").strip()
    voice_mode = os.getenv("STUDIO_VOICE_MODE", "blend").strip()

    my_pitch_spec = os.getenv("STUDIO_MY_PITCH", "-2").strip()
    my_level = float(os.getenv("STUDIO_MY_LEVEL", "100").strip())
    malak_pitch = os.getenv("STUDIO_MALAK_PITCH", "0").strip()
    malak_level = float(os.getenv("STUDIO_MALAK_LEVEL", "60").strip())

    echo_delay = float(os.getenv("STUDIO_ECHO_DELAY", "150").strip())
    echo_repeats = int(os.getenv("STUDIO_ECHO_REPEATS", "3").strip())
    echo_decay = float(os.getenv("STUDIO_ECHO_DECAY", "0.5").strip())
    echo_wet = float(os.getenv("STUDIO_ECHO_WET", "35").strip())
    tail_fade = float(os.getenv("STUDIO_TAIL_FADE", "1.5").strip())

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    is_quran_source = (source_mode == "quran")
    gap_sec = 0.35 if is_quran_source else 0.20

    gap_wav = WORK_DIR / "gap.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono",
         "-t", str(gap_sec), "-c:a", "pcm_s16le", str(gap_wav)],
        check=True, capture_output=True,
    )

    units: list[dict] = []
    diac_text_full = ""

    if is_quran_source:
        s_num, s_name, max_ayah = resolve_surah(surah_input)
        ayah_list = parse_ayahs(ayahs_input, max_ayah)
        reciter_label = RECITER_NAMES.get(reciter_id, reciter_id)
        base_slug = f"s{s_num:03d}"
        ep_title = f"سورة {s_name}"
        source_desc = f"📖 سورة {s_name} | الآيات: {ayahs_input} | القارئ: {reciter_label}"
        for a in ayah_list:
            in_wav = WORK_DIR / f"orig_{s_num}_{a}.wav"
            download_ayah(s_num, a, reciter_id, in_wav)
            units.append({
                "key": f"{s_num}_{a}",
                "wav": in_wav,
                "surah": s_num,
                "ayah": a,
                "surah_name": s_name,
                "text": f"{s_name}: {a}",
                "is_quran": True,
            })
    else:
        do_diac = (source_mode == "custom_text")
        units, diac_text_full = prepare_custom_text_units(custom_text, do_diac, gemini_voice)
        base_slug = "text"
        ep_title = "نص صوتي خاص"
        source_desc = f"✍️ نص يدوي ({'تشكيل تلقائي' if do_diac else 'مشكول مسبقاً'}) | أساس Gemini: {gemini_voice}"

    print("=" * 60)
    print(f"🎙️ استوديو البصمة والصدى | {source_desc}")
    print(f"🔊 الصدى: delay={echo_delay}ms | repeats={echo_repeats} | decay={echo_decay} | wet={echo_wet}% | tail={tail_fade}s")
    print("=" * 60)

    my_dry_map: dict[str, Path] = {}
    my_air_dry_map: dict[str, Path] = {}
    malak_dry_map: dict[str, Path] = {}

    need_my = voice_mode in ("my_voice", "compare_all") or (voice_mode == "blend" and my_level > 0)
    need_my_air = (voice_mode == "compare_all")
    need_malak = voice_mode in ("malak", "compare_all") or (voice_mode == "blend" and malak_level > 0)

    for u in units:
        k = u["key"]
        in_wav = u["wav"]
        if need_my:
            print(f"🔹 تحويل {k} ببصمتي المطوّرة (pitch={my_pitch_spec})...", flush=True)
            m_dry = WORK_DIR / f"mydry_{k}.wav"
            build_my_voice_dry(in_wav, m_dry, my_pitch_spec, use_air_blend=False)
            my_dry_map[k] = m_dry

        if need_my_air:
            print(f"💎 تحويل {k} ببصمتي + وضوح مخارج القارئ (pitch={my_pitch_spec})...", flush=True)
            ma_dry = WORK_DIR / f"myair_{k}.wav"
            build_my_voice_dry(in_wav, ma_dry, my_pitch_spec, use_air_blend=True)
            my_air_dry_map[k] = ma_dry

        if need_malak:
            print(f"🔸 تحويل {k} ببصمة ملك (pitch_spec={malak_pitch})...", flush=True)
            k_dry = WORK_DIR / f"malakdry_{k}.wav"
            build_malak_dry(in_wav, k_dry, malak_pitch)
            malak_dry_map[k] = k_dry

    modes_to_export = ["my_voice", "my_voice_air", "malak", "blend"] if voice_mode == "compare_all" else [voice_mode]
    manifest_outputs = []

    for m in modes_to_export:
        final_wavs = []
        meta_chunks = []

        for idx, u in enumerate(units):
            k = u["key"]
            is_last = (idx == len(units) - 1)
            dry_mix = WORK_DIR / f"mix_{m}_{k}.wav"

            if m == "original_echo":
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(u["wav"]),
                     "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95",
                     "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(dry_mix)],
                    check=True, capture_output=True,
                )
            elif m == "my_voice":
                shutil.copyfile(my_dry_map[k], dry_mix)
            elif m == "my_voice_air":
                shutil.copyfile(my_air_dry_map[k], dry_mix)
            elif m == "malak":
                shutil.copyfile(malak_dry_map[k], dry_mix)
            elif m == "blend":
                mix_voices(my_dry_map.get(k), malak_dry_map.get(k), my_level, malak_level, dry_mix)

            if is_quran_source:
                unit_tail = tail_fade if is_last else min(tail_fade, 0.90)
            else:
                unit_tail = tail_fade if is_last else min(tail_fade, 0.35)

            echoed = WORK_DIR / f"echo_{m}_{k}.wav"
            apply_custom_echo(dry_mix, echoed, echo_delay, echo_repeats, echo_decay, echo_wet, unit_tail)
            dur = get_dur(echoed)
            final_wavs.extend([echoed, gap_wav])

            if u["is_quran"]:
                meta_chunks.append({
                    "surah": u["surah"],
                    "ayah": u["ayah"],
                    "surah_name": u["surah_name"],
                    "reciter": "",
                    "text": u["text"],
                    "duration": round(dur + gap_sec, 3),
                })
            else:
                meta_chunks.append({
                    "text": u["text"],
                    "duration": round(dur + gap_sec, 3),
                })

        concat_file = WORK_DIR / f"concat_{m}.txt"
        concat_file.write_text("\n".join(f"file '{p.resolve()}'" for p in final_wavs), encoding="utf-8")

        out_mp3 = OUT_DIR / f"{base_slug}_{m}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
             "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)],
            check=True, capture_output=True, timeout=300,
        )

        seg_entry = {
            "index": 0,
            "type": "quran" if is_quran_source else "narration",
            "display": "",
            "reciter": "",
            "chunks": meta_chunks,
        }
        if is_quran_source:
            seg_entry["surah"] = units[0]["surah"]
            seg_entry["surah_name"] = units[0]["surah_name"]
            seg_entry["ayah_numbers"] = [u["ayah"] for u in units]
            seg_entry["display"] = f"{units[0]['surah_name']}: {units[0]['ayah']}"

        meta_path = OUT_DIR / f"{base_slug}_{m}_meta.json"
        meta_path.write_text(
            json.dumps({
                "episode": 1,
                "title": ep_title,
                "story_id": f"studio_{m}",
                "total_duration": get_dur(out_mp3),
                "segments": [seg_entry],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        orig_label = RECITER_NAMES.get(reciter_id, reciter_id) if is_quran_source else gemini_voice
        mode_labels = {
            "my_voice":      f"🎙️ بصمتي المطوّرة (pitch={my_pitch_spec})",
            "my_voice_air":  f"💎 بصمتي + وضوح مخارج القارئ (pitch={my_pitch_spec})",
            "malak":         f"✨ بصمة ملك (pitch={malak_pitch})",
            "blend":         f"🎚️ مزيج ({my_level:.0f}% بصمتي + {malak_level:.0f}% ملك)",
            "original_echo": f"📖 الأصل بالصدى فقط ({orig_label})",
        }
        caption = (
            f"{mode_labels[m]}\n"
            f"{source_desc}\n"
            f"🎛️ باتش بصمتي: {my_pitch_spec} | باتش ملك: {malak_pitch}\n"
            f"🔊 الصدى: زمن {echo_delay:.0f}ms | تكرار {echo_repeats} | تلاشي {echo_decay} | نسبة {echo_wet:.0f}% | ذيل {tail_fade}s"
        )
        manifest_outputs.append({
            "mode": m,
            "mp3": str(out_mp3),
            "title": ep_title,
            "performer": mode_labels[m],
            "caption": caption,
        })
        print(f"✅ تم تجهيز: {out_mp3.name}")

    (OUT_DIR / "manifest.json").write_text(
        json.dumps({
            "diacritized_text": diac_text_full,
            "outputs": manifest_outputs,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shutil.rmtree(WORK_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
