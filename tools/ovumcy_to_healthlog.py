"""Turn an Ovumcy JSON export into a file for 设置 → 导入.

    python tools/ovumcy_to_healthlog.py ovumcy-export.json health-log-import.json

The field names and values follow Ovumcy's export contract:
https://github.com/ovumcy/ovumcy-web/blob/main/docs/export.md#json-export

Nothing is dropped and nothing is guessed silently:
- fields with a matching built-in item become that item (月经, 经量, 体温, 症状, 生活…);
- "Spotting:N" lines in Ovumcy notes become the 经量 level on that day;
- spotting ticked without an amount becomes a hidden 血量 item, so the calendar
  still shows the day as spotting;
- `last_period_start` (a start with no logged day) becomes a 月经 day;
- everything else (mood rating, values we have no item for) is kept as text in
  that day's 笔记, next to the original note;
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.seed import TOGGLE_LISTS  # noqa: E402  (built-in item names -> stable ids)

BUILTIN = {name: f"{cat}_{i:02d}" for cat, names in TOGGLE_LISTS.items() for i, name in enumerate(names, start=1)}

# Ovumcy flow -> our 经量 level (少 1 / 中 2 / 多 3 / 过多 4)
FLOW = {"spotting": 0.5, "light": 1, "medium": 2, "heavy": 3}

# Ovumcy's 16 built-in symptoms; spotting is handled with 经量
SYMPTOMS = {
    "cramps": "绞痛", "headache": "头痛", "acne": "长痘", "mood": "情绪波动", "bloating": "腹胀",
    "fatigue": "疲劳", "breast_tenderness": "乳房胀痛", "back_pain": "背痛", "nausea": "恶心",
    "irritability": "烦躁", "insomnia": "失眠", "diarrhea": "腹泻", "constipation": "便秘",
}
SYMPTOM_TEXT = {"food_cravings": "嘴馋", "swelling": "水肿"}  # no item for these: kept as text

# Ovumcy cycle factors (closed list) -> (our item name, text kept when the meaning is only close)
FACTORS = {
    "stress": ("压力事件", None),
    "illness": ("生病", None),
    "travel": ("旅行/时差", None),
    "sleep_disruption": ("熬夜", "睡眠被打乱"),
}
FACTOR_TEXT = {"medication_change": "换药 / 调整用药"}

MUCUS = {"dry": "干", "creamy": "奶油状", "eggwhite": "蛋清状"}
MUCUS_TEXT = {"moist": "湿润"}  # no matching item
PREGNANCY = {"negative": "阴性", "positive": "阳性"}
SEX = {"protected": "有保护", "unprotected": "没有保护"}
BBT_RANGE = (30.0, 45.0)  # °C; anything else is kept as text rather than as a reading

# words in a note that tick a 生活 item
KEYWORDS = [("外出", ("外出", "出门")), ("冷饮/冰淇淋", ("冰淇淋", "冷饮")), ("运动", ("运动",))]

UNMEASURED_SPOTTING = {
    "id": "ovumcy_spotting", "category_id": "bleeding", "name": "spotting（没记量）", "kind": "toggle",
    "unit": None, "default_value": None, "options": None, "sort": 99, "hidden": True, "on_strip": False,
}
SPOTTING_LINE = re.compile(r"^\s*spotting\s*[:：]\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def convert(data: dict) -> tuple[dict, dict]:
    entries: list[dict] = []
    report: dict = {"items": Counter(), "keyword_days": [], "as_text": [], "unmeasured_spotting": [],
                    "added_starts": []}
    uses_unmeasured = False

    def add(day: str, item_id: str, value=None, choice=None, text=None):
        entries.append({"date": day, "item_id": item_id, "value": value, "choice": choice,
                        "text": text, "time": None, "source": "manual"})
        report["items"][item_id] += 1

    period_days = set()
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
            period_days.add(day)

        flow = e.get("flow") or "none"
        if measured is not None:
            add(day, "flow", value=min(max(measured, 0.1), 9.9))
        elif flow in FLOW:
            add(day, "flow", value=FLOW[flow])
        elif flow != "none":
            extra.append(f"经量：{flow}")
        elif (e.get("symptoms") or {}).get("spotting") and day not in period_days:
            add(day, UNMEASURED_SPOTTING["id"])
            uses_unmeasured = True
            report["unmeasured_spotting"].append(day)

        bbt = e.get("bbt")
        if isinstance(bbt, (int, float)) and bbt:  # absent, null and 0 all mean "not measured"
            if BBT_RANGE[0] <= bbt <= BBT_RANGE[1]:
                add(day, "temperature", value=round(float(bbt), 2))
            else:
                extra.append(f"Ovumcy 体温读数：{bbt}")

        for name, on in (e.get("symptoms") or {}).items():
            if not on or name == "spotting":
                continue
            if name in SYMPTOMS:
                add(day, BUILTIN[SYMPTOMS[name]])
            else:
                extra.append(f"症状：{SYMPTOM_TEXT.get(name, name)}")
        for other in e.get("other_symptoms") or []:
            if other in BUILTIN:
                add(day, BUILTIN[other])
            else:
                extra.append(f"其他症状：{other}")

        for factor in e.get("cycle_factors") or []:
            if factor in FACTORS:
                name, text = FACTORS[factor]
                add(day, BUILTIN[name], text=text)
            else:
                extra.append(f"周期因素：{FACTOR_TEXT.get(factor, factor)}")

        mucus = e.get("cervical_mucus") or "none"
        if mucus in MUCUS:
            add(day, BUILTIN[MUCUS[mucus]])
        elif mucus != "none":
            extra.append(f"分泌物：{MUCUS_TEXT.get(mucus, mucus)}")

        test = e.get("pregnancy_test") or "none"
        if test in PREGNANCY:
            add(day, "pregnancy", choice=PREGNANCY[test])
        elif test != "none":
            extra.append(f"验孕：{test}")

        sex = e.get("sex_activity") or "none"
        if sex != "none":
            add(day, BUILTIN["性生活"], text=SEX.get(sex, sex))

        if e.get("mood_rating"):
            extra.append(f"Ovumcy 心情评分：{e['mood_rating']}")
        if e.get("is_uncertain"):
            extra.append("Ovumcy 标记为不确定")

        tagged = [BUILTIN[name] for name, words in KEYWORDS if any(w in note for w in words)]
        already = {x["item_id"] for x in entries if x["date"] == day}
        new_tags = [item_id for item_id in tagged if item_id not in already]
        for item_id in new_tags:
            add(day, item_id)
        if new_tags:
            report["keyword_days"].append((day, new_tags))

        text = "\n".join(part for part in (note, *extra) if part)
        if text:
            add(day, "note", text=text)
        if extra:
            report["as_text"].append((day, extra))

    # the stored onboarding start, when no logged day carries it
    start = data.get("last_period_start")
    if isinstance(start, str) and start and start not in period_days:
        add(start, "period", value=1)
        report["added_starts"].append(start)
        entries.sort(key=lambda x: x["date"])

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
    if report["added_starts"]:
        print("period start with no logged period day, added as a 月经 day:", ", ".join(report["added_starts"]))
    if report["unmeasured_spotting"]:
        print("spotting without an amount:", ", ".join(report["unmeasured_spotting"]))
    for day, tagged in report["keyword_days"]:
        print(f"ticked from note words {day}: {', '.join(tagged)}")
    for day, extra in report["as_text"]:
        print(f"kept as text {day}: {' / '.join(extra)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
