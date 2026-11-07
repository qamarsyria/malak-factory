import os
import re
import sys
import json
import random
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "whispers" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_whisper.txt"

WHISPERS_BANK = {
    "romantic_malak": [
        "إلى مَلَك... يا من سكنتِ القلب بلا استئذان، وزرعتِ في الروح بستاناً من الأمان. يا أجمل ما كتبه الله في قدري، ويا رحمةً أهداها الله لقلبي. أحبكِ حباً يليق بكِ، وأسأل الله أن يجمعنا في الدنيا على طاعته، وفي الآخرة على رؤيته.",
        "مَلَك... اسمٌ كأنه همسةٌ من السماء، ونورٌ أضاء ظلمات ليلي. يا من جعلتِ العاديَّ في حياتي شيئاً استثنائياً، وعلّمتِني أن الحب الحقيقي هو الذي يقرب إلى الله. أسأل الله أن يحفظكِ لي، وأن يجعلني خيرَ من يمشي على الأرض معكِ."
    ],
    "romantic_maryam": [
        "إلى مريم... يا من جعلتِ من قلبي وطناً، ومن أيامي عيداً. يا طيبةَ القلب، ويا رقيقةَ الروح. أحبكِ حباً لو وزّع على العالم لكفاه. أسأل الله أن يديمكِ نعمةً في حياتي، وأن يجعل بيننا مودةً ورحمة.",
        "مريم... يا أجمل ما كتبه الله في اللوح المحفوظ من أجلي. يا من اسمكِ يحمل عبق التاريخ، وقلبكِ يحمل نور الإيمان. أسأل الله أن يحفظكِ من كل سوء، وأن يجمعني بكِ على خير."
    ],
    "romantic_malak_2": [
        "إلى مَلَاك... يا من اسمكِ يحمل نور السماء، وقلبكِ يحمل طيبة الأرض. يا من جعلتِ العاديَّ في حياتي شيئاً استثنائياً. أسأل الله أن يحفظكِ لي، وأن يجعلني خيرَ من يمشي على الأرض معكِ.",
        "مَلَاك... يا همسةً في القلب، ويا نوراً في العين. أنتِ أجمل ما كتبه الله في قدري. أسأل الله أن يحفظكِ من كل سوء."
    ],
    "dua_malak": [
        "اللهم إني أسألك باسمك الأعظم، أن تحفظ مَلَك، وأن تبارك في عمرها وعملها ورزقها. اللهم اجعلها من عبادك الصالحين، ومن الذين لا خوفٌ عليهم ولا هم يحزنون.",
        "اللهم يا من جمعت القلوب، اجمع بيني وبين مَلَك على خير، في الدنيا على طاعتك، وفي الآخرة على رؤيتك. اللهم اكتب لنا من الأجر ما لا عينٌ رأت ولا أذنٌ سمعت."
    ],
    "dua_maryam": [
        "اللهم إني أسألك أن تجعل مريم من الذاكرين الشاكرين الصابرين، وأن تجعلها من أهل الفردوس الأعلى. اللهم احفظها بحفظك، وبارك في عمرها وعملها.",
        "اللهم اجعل مريم من المقربين، ومن الذين لا خوفٌ عليهم ولا هم يحزنون. اللهم ارزقها من حيث لا تحتسب."
    ],
    "dua_malak_2": [
        "اللهم إني أسألك أن تحفظ مَلَاك، وأن تبارك في عمرها وعملها ورزقها. اللهم اجعلها من عبادك الصالحين، ومن الذين لا خوفٌ عليهم ولا هم يحزنون.",
        "اللهم اجعل مَلَاك من الذاكرين الشاكرين الصابرين، وأن تجعلها من أهل الفردوس الأعلى. اللهم احفظها بحفظك."
    ],
    "quotes_malak": [
        "قال الشاعر: يا من رأى القمرَ استحى من نورها... ونجومَ ليلٍ قد أضاءت من خدودِ مَلَك. أحبكِ حباً يليق بكِ.",
        "قال الشاعر: مَلَكٌ في القلبِ ساكنةٌ... وروحي في هواها ذائبة. أسأل الله أن يديمها نعمةً."
    ],
    "quotes_maryam": [
        "قال الشاعر: مريمُ... يا أجملَ الأسماءِ في قلبي... ويا نورَ العيونِ إذا رأت. أحبكِ حباً يليق بكِ.",
        "قال الشاعر: مريمُ... أنتِ الحلمُ في ليلِ الانتظارِ... والفجرُ بعدَ طولِ السُّهادِ."
    ],
    "quotes_malak_2": [
        "قال الشاعر: مَلَاكُ... يا من سكنتِ الروحَ والوجدانا... ويا من جعلتِ القلبَ بستانا. أسأل الله أن يحفظكِ.",
        "قال الشاعر: مَلَاكُ... يا همسةَ القلبِ ويا نورَ العيان... ويا من اسمها كالهمسِ في الأوطانِ."
    ]
}

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    category = random.choice(list(WHISPERS_BANK.keys()))
    text = random.choice(WHISPERS_BANK[category])
    
    if "romantic" in category or "quotes" in category:
        content_title = "همسات ملك - خاطرة"
        category_type = "romantic"
    elif "dua" in category:
        content_title = "همسات ملك - دعاء"
        category_type = "religious"
    else:
        content_title = "همسات ملك - أذكار"
        category_type = "religious"
    
    segments = []
    segments.append({
        "type": "narration",
        "text": text,
        "visual": "rose garden butterflies" if category_type == "romantic" else "islamic architecture mosque sunset"
    })
    
    ep_data = {
        "episode_number": 1,
        "story_id": "whispers",
        "title": content_title,
        "category": category_type,
        "segments": segments
    }
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"whisper-{timestamp}.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم إنشاء همسة: {output_path}", flush=True)

if __name__ == "__main__":
    main()
