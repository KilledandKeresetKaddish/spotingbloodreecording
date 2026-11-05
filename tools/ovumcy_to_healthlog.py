"""Turn an Ovumcy JSON export into a file for 设置 → 导入.

    python tools/ovumcy_to_healthlog.py ovumcy-export.json health-log-import.json

Nothing is dropped and nothing is guessed silently:
- fields with a matching built-in item become that item (月经, 经量, 症状, 生活…);
- "Spotting:N" lines in Ovumcy notes become the 经量 level on that day;
- spotting ticked without an amount becomes a hidden 血量 item, so the calendar
  still shows the day as spotting;
- everything else (mood rating, symptoms we have no item for, unknown values)
  is kept as text in that day's 笔记, next to the original note;
- the words 外出/出门, 冰淇淋/冷饮 and 运动 in a note also tick those 生活 items.
A summary of what went where is printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone

# Ovumcy value -> our 经量 level (少 1 / 中 2 / 多 3 / 过多 4)
FLOW = {"spotting": 0.5, "light": 1, "medium": 2, "heavy": 3}

# Ovumcy symptom -> our built-in toggle (see app/seed.py for the ids)
SYMPTOMS = {
    "cramps": "symptoms_02",            # 绞痛
    "headache": "symptoms_03",          # 头痛
    "back_pain": "symptoms_04",         # 背痛
    "insomnia": "symptoms_05",          # 失眠
    "breast_tenderness": "symptoms_08",  # 乳房胀痛
    "bloating": "symptoms_09",          # 腹胀
    "constipation": "symptoms_10",      # 便秘
    "diarrhea": "symptoms_11",          # 腹泻
    "nausea": "symptoms_12",            # 恶心
    "fatigue": "symptoms_14",           # 疲劳
    "acne": "symptoms_15",              # 长痘
    "irritability": "mood_06",          # 烦躁
    "mood": "mood_09",                  # 情绪波动
}
SYMPTOM_TEXT = {"food_cravings": "嘴馋", "swelling": "水肿"}  # no item for these: kept as text

# Ovumcy cycle factor -> (our toggle, text kept on it when the meaning is only close)
FACTORS = {
    "illness": ("life_08", None),                    # 生病
    "stress": ("life_09", None),                     # 压力事件
    "sleep_disruption": ("life_06", "睡眠被打乱"),     # 熬夜
}
MUCUS = {"dry": "discharge_01", "sticky": "discharge_02", "creamy": "discharge_03",
         "watery": "discharge_04", "eggwhite": "discharge_05", "egg_white": "discharge_05"}
PREGNANCY = {"negative": "阴性", "positive": "阳性", "invalid": "无效"}

# words in a note that tick a 生活 item
KEYWORDS = [("life_01", ("外出", "出门")), ("life_03", ("冰淇淋", "冷饮")), ("life_02", ("运动",))]

UNMEASURED_SPOTTING = {
    "id": "ovumcy_spotting", "category_id": "bleeding", "name": "spotting（没记量）", "kind": "toggle",
    "unit": None, "default_value": None, "options": None, "sort": 99, "hidden": True, "on_strip": False,
}
SPOTTING_LINE = re.compile(r"^\s*spotting\s*[:：]\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def convert(data: dict) -> tuple[dict, dict]:
    entries: list[dict] = []
    report: dict = {"items": Counter(), "keyword_days": [], "as_text": [], "unmeasured_spotting": []}
    uses_unmeasured = False

    def add(day: str, item_id: str, value=None, choice=None, text=None):
        entries.append({"date": day, "item_id": item_id, "value": value, "choice": choice,
                        "text": text, "time": None, "source": "manual"})
        report["items"][item_id] += 1

    for e in sorted(data.get("entries", []), key=lambda x: x["date"]):
        day = e["date"]
        extra: list[str] = []  # text that ends up in the day's note

        # notes: pull out "Spotting:N", keep the rest word for word
        note_lines, measured = [], None
        for line in (e.get("notes") or "").splitlines():
            m = SPOTTING_LINE.match(line)
            if m:
                measured = float(m.group(1))
            elif line.strip():
                note_lines.append(line.rstrip())
        note = "\n".join(note_lines)

        if e.get("period"):
            add(day, "period", value=1)

        flow = e.get("flow") or "none"
        if measured is not None:
            add(day, "flow", value=min(max(measured, 0.1), 9.9))
        elif flow in FLOW:
            add(day, "flow", value=FLOW[flow])
        elif flow != "none":
            extra.append(f"经量：{flow}")
        elif (e.get("symptoms") or {}).get("spotting") and not e.get("period"):
            add(day, UNMEASURED_SPOTTING["id"])
            uses_unmeasured = True
            report["unmeasured_spotting"].append(day)

        for name, on in (e.get("symptoms") or {}).items():
            if not on or name == "spotting":
                continue
            if name in SYMPTOMS:
                add(day, SYMPTOMS[name])
            else:
                extra.append(f"症状：{SYMPTOM_TEXT.get(name, name)}")
        for other in e.get("other_symptoms") or []:
            extra.append(f"其他症状：{other}")

        for factor in e.get("cycle_factors") or []:
            if factor in FACTORS:
                item_id, text = FACTORS[factor]
                add(day, item_id, text=text)
            else:
                extra.append(f"周期因素：{factor}")

        mucus = e.get("cervical_mucus") or "none"
        if mucus in MUCUS:
            add(day, MUCUS[mucus])
        elif mucus != "none":
            extra.append(f"分泌物：{mucus}")

        test = e.get("pregnancy_test") or "none"
        if test in PREGNANCY:
            add(day, "pregnancy", choice=PREGNANCY[test])
        elif test != "none":
            extra.append(f"验孕：{test}")

        sex = e.get("sex_activity") or "none"
        if sex != "none":
            add(day, "sex_01", text=f"Ovumcy：{sex}")

        if e.get("mood_rating"):
            extra.append(f"Ovumcy 心情评分：{e['mood_rating']}")
        if e.get("is_uncertain"):
            extra.append("Ovumcy 标记为不确定")

        tagged = [item_id for item_id, words in KEYWORDS if any(w in note for w in words)]
        already = {x["item_id"] for x in entries if x["date"] == day}
        for item_id in tagged:
            if item_id not in already:
                add(day, item_id)
        if tagged:
            report["keyword_days"].append((day, tagged))

        text = "\n".join(part for part in (note, *extra) if part)
        if text:
            add(day, "note", text=text)
        if extra:
            report["as_text"].append((day, extra))

    out = {
        "format": "health-log",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "ovumcy",
        "categories": [],
        "items": [UNMEASURED_SPOTTING] if uses_unmeasured else [],
        "entries": entries,
    }
    return out, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="Ovumcy export (.json)")
    parser.add_argument("target", help="file to write for 设置 → 导入")
    args = parser.parse_args(argv)
    with open(args.source, encoding="utf-8") as f:
        data = json.load(f)
    out, report = convert(data)
    with open(args.target, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    days = sorted({x["date"] for x in out["entries"]})
    print(f"{len(data.get('entries', []))} Ovumcy days -> {len(out['entries'])} entries on {len(days)} days")
    for item_id, n in sorted(report["items"].items()):
        print(f"  {item_id:<16} {n}")
    if report["unmeasured_spotting"]:
        print("spotting without an amount:", ", ".join(report["unmeasured_spotting"]))
    for day, tagged in report["keyword_days"]:
        print(f"ticked from note words {day}: {', '.join(tagged)}")
    for day, extra in report["as_text"]:
        print(f"kept as text {day}: {' / '.join(extra)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
