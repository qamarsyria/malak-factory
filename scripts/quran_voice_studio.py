"""
quran_voice_studio.py — استوديو خاص للتحكم الكامل ببصمتي (v4.0-clean + v3.1) وبصمة ملك ومزجهما
- يدعم اختيار القارئ، اسم السورة بالعربي أو رقمها، والآيات بأي صيغة (1-5 أو 1,3,7).
- يدعم مزج البصمتين والتحكم بنسبة ارتفاع صوت كل بصمة من 0% إلى 200%.
- يدعم التحكم الدقيق بالباتش، وزمن الصدى (ms)، وعدد التكرارات، ومعامل التلاشي، ونسبة صوت الصدى، وذيل التلاشي.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = _REPO_ROOT / "out" / "studio"
WORK_DIR = _REPO_ROOT / "temp_studio_work"
RVC_DIR = _REPO_ROOT / "rvc"

QURAN_BASE_URL = "https://everyayah.com/data"
TARGET_SR = "48000"
AYAH_GAP_SEC = 0.35

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

# قائمة احتياطية كاملة بأسماء السور الـ 114 وعدد آياتها
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


def build_my_voice_dry(in_48k: Path, out_dry: Path, base_pitch: int):
    """بصمتي (87.5% v4.0-clean + 12.5% v3.1-myvoice) قبل إضافة الصدى."""
    clean_w = RVC_DIR / "assets/weights/clean.pth"
    rough_w = RVC_DIR / "assets/weights/rough.pth"
    if not clean_w.exists():
        raise RuntimeError("❌ ملف بصمتي clean.pth غير موجود")

    clean_out = out_dry.with_name(out_dry.stem + "_c.wav")
    run_rvc_cli(in_48k, clean_out, "assets/weights/clean.pth", "clean.index", base_pitch, 0.45, 0.4, 0.8)

    if rough_w.exists():
        rough_out = out_dry.with_name(out_dry.stem + "_r.wav")
        run_rvc_cli(in_48k, rough_out, "assets/weights/rough.pth", "rough.index", base_pitch + 2, 0.45, 0.4, 0.8)
        fc = (
            "[0:a]volume=0.875[a0];"
            "[1:a]volume=0.125[a1];"
            "[a0][a1]amix=inputs=2:duration=longest:normalize=0,"
            "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(clean_out), "-i", str(rough_out),
             "-filter_complex", fc, "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(out_dry)],
            check=True, capture_output=True,
        )
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(clean_out), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95",
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
            print(f"   🎯 تحليل طبقة القارئ لملك: {input_hz:.1f} Hz | Auto={auto_pitch}", flush=True)
    except Exception as e:
        print(f"   ⚠️ تعذر التحليل التلقائي لطبقة ملك ({e}) — استخدام الافتراضي 4", flush=True)

    final_pitch = max(-4, min(14, auto_pitch + offset))
    print(f"   🔥 باتش ملك النهائي: {final_pitch} (offset={offset:+d})", flush=True)
    return final_pitch


def build_malak_dry(in_48k: Path, out_dry: Path, pitch_spec: str):
    """بصمة ملك (تبطيء 25% + تحليل 190Hz + RVC + إعادة السرعة + EQ ملك)."""
    malak_w = RVC_DIR / "assets/weights/malak.pth"
    if not malak_w.exists():
        raise RuntimeError("❌ ملف بصمة ملك (malak_voice.pth) غير موجود في المستودع ولا في Releases!")

    tempo_slow = 0.75
    tempo_fast = 1.0 / tempo_slow
    pre_cleaned = out_dry.with_name(out_dry.stem + "_pre.wav")
    rvc_raw = out_dry.with_name(out_dry.stem + "_raw.wav")

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(in_wav_or := in_48k),
         "-af", f"highpass=f=60,lowpass=f=15500,afftdn=nr=2:nf=-50,atempo={tempo_slow}",
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(pre_cleaned)],
        check=True, capture_output=True,
    )
    del in_wav_or

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
    """صدى استوديو احترافي بالتحكم بزمن الصدى وعدد التكرارات والتلاشي ونسبة صوت الصدى."""
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
    reciter_id = os.getenv("STUDIO_RECITER", "Husary_128kbps").strip()
    surah_input = os.getenv("STUDIO_SURAH", "الكهف").strip()
    ayahs_input = os.getenv("STUDIO_AYAHS", "1-5").strip()
    voice_mode = os.getenv("STUDIO_VOICE_MODE", "blend").strip()

    my_pitch = int(os.getenv("STUDIO_MY_PITCH", "-6").strip())
    my_level = float(os.getenv("STUDIO_MY_LEVEL", "100").strip())
    malak_pitch = os.getenv("STUDIO_MALAK_PITCH", "0").strip()
    malak_level = float(os.getenv("STUDIO_MALAK_LEVEL", "60").strip())

    echo_delay = float(os.getenv("STUDIO_ECHO_DELAY", "150").strip())
    echo_repeats = int(os.getenv("STUDIO_ECHO_REPEATS", "3").strip())
    echo_decay = float(os.getenv("STUDIO_ECHO_DECAY", "0.5").strip())
    echo_wet = float(os.getenv("STUDIO_ECHO_WET", "35").strip())
    tail_fade = float(os.getenv("STUDIO_TAIL_FADE", "1.5").strip())

    s_num, s_name, max_ayah = resolve_surah(surah_input)
    ayah_list = parse_ayahs(ayahs_input, max_ayah)
    reciter_label = RECITER_NAMES.get(reciter_id, reciter_id)

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    gap_wav = WORK_DIR / "gap.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono",
         "-t", str(AYAH_GAP_SEC), "-c:a", "pcm_s16le", str(gap_wav)],
        check=True, capture_output=True,
    )

    print("=" * 60)
    print(f"🎙️ استوديو البصمة والصدى | سورة {s_name} ({s_num}) | الآيات: {ayah_list}")
    print(f"📖 القارئ: {reciter_label} | الوضع: {voice_mode}")
    print(f"🔊 الصدى: delay={echo_delay}ms | repeats={echo_repeats} | decay={echo_decay} | wet={echo_wet}% | tail={tail_fade}s")
    print("=" * 60)

    # 1) تحميل الآيات وتحويل الأصوات المطلوبة مرة واحدة لكل آية
    raw_ayahs: dict[int, Path] = {}
    my_dry_map: dict[int, Path] = {}
    malak_dry_map: dict[int, Path] = {}

    need_my = voice_mode in ("my_voice", "compare_all") or (voice_mode == "blend" and my_level > 0)
    need_malak = voice_mode in ("malak", "compare_all") or (voice_mode == "blend" and malak_level > 0)

    for a in ayah_list:
        in_wav = WORK_DIR / f"orig_{s_num}_{a}.wav"
        download_ayah(s_num, a, reciter_id, in_wav)
        raw_ayahs[a] = in_wav

        if need_my:
            print(f"🔹 تحويل آية {s_num}:{a} ببصمتي (pitch={my_pitch})...", flush=True)
            m_dry = WORK_DIR / f"mydry_{s_num}_{a}.wav"
            build_my_voice_dry(in_wav, m_dry, my_pitch)
            my_dry_map[a] = m_dry

        if need_malak:
            print(f"🔸 تحويل آية {s_num}:{a} ببصمة ملك (pitch_spec={malak_pitch})...", flush=True)
            k_dry = WORK_DIR / f"malakdry_{s_num}_{a}.wav"
            build_malak_dry(in_wav, k_dry, malak_pitch)
            malak_dry_map[a] = k_dry

    modes_to_export = ["my_voice", "malak", "blend"] if voice_mode == "compare_all" else [voice_mode]
    manifest_outputs = []

    for m in modes_to_export:
        final_wavs = []
        meta_chunks = []

        for a in ayah_list:
            dry_mix = WORK_DIR / f"mix_{m}_{s_num}_{a}.wav"
            if m == "original_echo":
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(raw_ayahs[a]),
                     "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95",
                     "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(dry_mix)],
                    check=True, capture_output=True,
                )
            elif m == "my_voice":
                shutil.copyfile(my_dry_map[a], dry_mix)
            elif m == "malak":
                shutil.copyfile(malak_dry_map[a], dry_mix)
            elif m == "blend":
                mix_voices(
                    my_dry_map.get(a),
                    malak_dry_map.get(a),
                    my_level,
                    malak_level,
                    dry_mix,
                )

            echoed = WORK_DIR / f"echo_{m}_{s_num}_{a}.wav"
            apply_custom_echo(dry_mix, echoed, echo_delay, echo_repeats, echo_decay, echo_wet, tail_fade)
            dur = get_dur(echoed)
            final_wavs.extend([echoed, gap_wav])

            meta_chunks.append({
                "surah": s_num,
                "ayah": a,
                "surah_name": s_name,
                "reciter": reciter_label,
                "text": f"{s_name}: {a}",
                "duration": round(dur + AYAH_GAP_SEC, 3),
            })

        concat_file = WORK_DIR / f"concat_{m}.txt"
        concat_file.write_text("\n".join(f"file '{p.resolve()}'" for p in final_wavs), encoding="utf-8")

        out_mp3 = OUT_DIR / f"s{s_num:03d}_{m}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
             "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)],
            check=True, capture_output=True, timeout=300,
        )

        meta_path = OUT_DIR / f"s{s_num:03d}_{m}_meta.json"
        meta_path.write_text(
            json.dumps({
                "episode": 1,
                "title": f"سورة {s_name}",
                "story_id": f"studio_{m}",
                "total_duration": get_dur(out_mp3),
                "segments": [{
                    "index": 0,
                    "type": "quran",
                    "surah": s_num,
                    "surah_name": s_name,
                    "ayah_numbers": ayah_list,
                    "display": f"{s_name}: {ayah_list[0]}",
                    "reciter": "",
                    "chunks": meta_chunks,
                }],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        mode_labels = {
            "my_voice":      f"🎙️ بصمتي (pitch={my_pitch})",
            "malak":         f"✨ بصمة ملك (pitch={malak_pitch})",
            "blend":         f"🎚️ مزيج ({my_level:.0f}% بصمتي + {malak_level:.0f}% ملك)",
            "original_echo": f"📖 الأصل بالصدى فقط ({reciter_label})",
        }
        caption = (
            f"{mode_labels[m]}\n"
            f"📖 سورة {s_name} | الآيات: {ayahs_input} | القارئ: {reciter_label}\n"
            f"🎛️ باتش بصمتي: {my_pitch} | باتش ملك: {malak_pitch}\n"
            f"🔊 الصدى: زمن {echo_delay:.0f}ms | تكرار {echo_repeats} | تلاشي {echo_decay} | نسبة {echo_wet:.0f}% | ذيل {tail_fade}s"
        )
        manifest_outputs.append({
            "mode": m,
            "mp3": str(out_mp3),
            "title": f"سورة {s_name} ({ayahs_input})",
            "performer": mode_labels[m],
            "caption": caption,
        })
        print(f"✅ تم تجهيز: {out_mp3.name}")

    (OUT_DIR / "manifest.json").write_text(
        json.dumps({"outputs": manifest_outputs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shutil.rmtree(WORK_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
