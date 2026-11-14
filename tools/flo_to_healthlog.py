"""Turn a Flo data export (the account JSON) into a file for 设置 → 导入.

    python tools/flo_to_healthlog.py flo-export.json health-log-import.json [--before YYYY-MM-DD]

--before keeps only Flo days earlier than that date, for when another app's
records (e.g. Ovumcy) already cover the later part.

What is read:
- operationalData.cycles: period start..end (both days included) -> 月经 days,
  period_intensity {"day index": 1 low / 2 medium / 3 high} -> 经量 on that day;
- pointEventsManualData.point_events_manual_v2: basal temperature, weight, water,
  bleeding outside the period, discharge, symptoms, moods, sport, disturbers;
- operationalData.notes -> 笔记;
- operationalData.repeatable_child_point_events (medication taken) -> 笔记 lines.
Events meaning "nothing" (SexNone, NoSport) are skipped. Anything without a
matching item is kept as text in that day's 笔记. A summary is printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.seed import TOGGLE_LISTS  # noqa: E402

BUILTIN = {name: f"{cat}_{i:02d}" for cat, names in TOGGLE_LISTS.items() for i, name in enumerate(names, start=1)}

INTENSITY = {1: 1, 2: 2, 3: 3}  # Flo low / medium / high -> 少 / 中 / 多

# Flo (category, subcategory) seen in a real export -> (our built-in toggle, text kept when the meaning is only close).
# Anything else ends up as text in the note, so nothing is lost.
TOGGLES = {
    ("Symptom", "DrawingPain"): ("腹痛", "Flo：牵扯痛"),
    ("Symptom", "Bloating"): ("腹胀", None),
    ("Symptom", "Fatigue"): ("疲劳", None),
    ("Symptom", "Nausea"): ("恶心", None),
    ("Symptom", "Constipation"): ("便秘", None),
    ("Mood", "Happy"): ("开心", None),
    ("Mood", "Sad"): ("低落", "Flo：难过"),
    ("Mood", "Depressed"): ("低落", "Flo：抑郁"),
    ("Mood", "Angry"): ("烦躁", "Flo：生气"),
    ("Mood", "Panic"): ("焦虑", "Flo：惊恐"),
    ("Mood", "Swings"): ("情绪波动", None),
    ("Fluid", "Watery"): ("水样", None),
    ("Fluid", "Eggwhite"): ("蛋清状", None),
    ("Sport", "Gym"): ("运动", "Flo：健身房"),
    ("Disturber", "DiseaseOrTrauma"): ("生病", "Flo：疾病或外伤"),
    ("Disturber", "Stress"): ("压力事件", None),
}
TEXT = {("Mood", "ObsessiveThoughts"): "强迫思维", ("Ovulation", "OtherMethods"): "标记了排卵（其他方法）"}
NOTHING = {("Sex", "SexNone"), ("Sport", "NoSport")}

UNMEASURED_SPOTTING = {  # same hidden item the Ovumcy converter uses
    "id": "ovumcy_spotting", "category_id": "bleeding", "name": "spotting（没记量）", "kind": "toggle",
    "unit": None, "default_value": None, "options": None, "sort": 99, "hidden": True, "on_strip": False,
}


def day_of(stamp: str) -> str:
    return stamp[:10]


def convert(data: dict, before: str | None = None) -> tuple[dict, dict]:
    op = data.get("operationalData", {})
    keep = (lambda d: d < before) if before else (lambda d: True)
    report: dict = {"items": Counter(), "as_text": Counter(), "skipped_nothing": 0, "unknown": Counter(),
                    "cycles": 0, "dropped_after": 0}
    entries: list[dict] = []
    notes: dict[str, list[str]] = defaultdict(list)
    toggles: dict[tuple[str, str], list[str]] = defaultdict(list)  # (day, item) -> texts
    single: dict[tuple[str, str], float] = {}                      # (day, item) -> value
    water: dict[str, float] = defaultdict(float)  # day -> ml; Flo logs have no usable time

    def add(day, item_id, value=None, text=None):
        entries.append({"date": day, "item_id": item_id, "value": value, "choice": None, "text": text,
                        "time": None, "source": "manual"})
        report["items"][item_id] += 1

    # periods
    period_days: set[str] = set()
    for c in op.get("cycles", []):
        if c.get("pregnant"):
            continue
        start = date.fromisoformat(day_of(c["period_start_date"]))
        end = date.fromisoformat(day_of(c["period_end_date"] or c["period_start_date"]))
        if not keep(start.isoformat()):
            report["dropped_after"] += 1
            continue
        report["cycles"] += 1
        for k in range((end - start).days + 1):
            d = (start + timedelta(days=k)).isoformat()
            if keep(d):
                period_days.add(d)
        for idx, level in json.loads(c.get("period_intensity") or "{}").items():
            d = (start + timedelta(days=int(idx))).isoformat()
            if keep(d) and level in INTENSITY:
                single[(d, "flow")] = INTENSITY[level]

    # manual day events
    for e in data.get("pointEventsManualData", {}).get("point_events_manual_v2", []):
        d = day_of(e.get("local_date") or e["date"])
        if not keep(d):
            continue
        key = (e["category"], e["subcategory"])
        props = e.get("properties") or {}
        if key in NOTHING:
            report["skipped_nothing"] += 1
        elif key == ("Temperature", "Basal") and props.get("value"):
            single[(d, "temperature")] = round(float(props["value"]), 2)
        elif key == ("Weight", "Weight") and props.get("value"):
            single[(d, "weight")] = round(float(props["value"]), 1)
        elif key == ("Water", "Water") and props.get("volume"):
            water[d] += float(props["volume"]) * 1000  # litres -> ml, summed per day
        elif key == ("Sleep", "Sleep") and props.get("value"):
            notes[d].append(f"Flo 睡眠：{int(props['value']) / 60:g} 小时")
            report["as_text"]["Sleep"] += 1
        elif key == ("Fluid", "Bloody"):
            if d not in period_days:
                toggles[(d, UNMEASURED_SPOTTING["id"])]  # bleeding with no amount -> spotting day
        elif key in TOGGLES:
            name, text = TOGGLES[key]
            toggles[(d, BUILTIN[name])].append(text or "")
        elif key in TEXT:
            notes[d].append(f"Flo：{TEXT[key]}")
            report["as_text"][key[1]] += 1
        else:
            notes[d].append(f"Flo：{key[0]} / {key[1]}" + (f" {json.dumps(props, ensure_ascii=False)}" if props else ""))
            report["unknown"][f"{key[0]}/{key[1]}"] += 1

    # free notes and medication taken
    for n in op.get("notes", []):
        d = day_of(n["date"])
        if keep(d) and (n.get("text") or "").strip():
            notes[d].insert(0, n["text"].strip())
    for m in op.get("repeatable_child_point_events", []):
        if m.get("deleted") or m.get("category") != "Medication":
            continue
        d = day_of(m["date"])
        if keep(d):
            title = json.loads(m.get("properties") or "{}").get("title", "药")
            notes[d].append(f"Flo 用药：{m['date'][11:16]} {title}")
            report["as_text"]["Medication"] += 1

    for d in sorted(period_days):
        add(d, "period", value=1)
    for (d, item_id), value in sorted(single.items()):
        add(d, item_id, value=value)
    for d, ml in sorted(water.items()):  # one entry per day, so the import cannot drop equal servings
        add(d, "water", value=round(ml))
    uses_unmeasured = False
    for (d, item_id), texts in sorted(toggles.items()):
        uses_unmeasured |= item_id == UNMEASURED_SPOTTING["id"]
        text = "；".join(dict.fromkeys(t for t in texts if t)) or None
        add(d, item_id, text=text)
    for d, lines in sorted(notes.items()):
        add(d, "note", text="\n".join(lines))
    entries.sort(key=lambda x: x["date"])

    out = {
        "format": "health-log",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "flo",
        "categories": [],
        "items": [UNMEASURED_SPOTTING] if uses_unmeasured else [],
        "entries": entries,
    }
    return out, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="Flo export (.json)")
    parser.add_argument("target", help="file to write for 设置 → 导入")
    parser.add_argument("--before", help="only keep Flo days before this date (YYYY-MM-DD)")
    args = parser.parse_args(argv)
    with open(args.source, encoding="utf-8") as f:
        data = json.load(f)
    out, report = convert(data, args.before)
    with open(args.target, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    days = sorted({x["date"] for x in out["entries"]})
    print(f"{report['cycles']} periods kept ({report['dropped_after']} on or after --before), "
          f"{len(out['entries'])} entries on {len(days)} days, {days[0] if days else '-'} .. {days[-1] if days else '-'}")
    for item_id, n in sorted(report["items"].items()):
        print(f"  {item_id:<16} {n}")
    print(f"skipped 'nothing' events (SexNone / NoSport): {report['skipped_nothing']}")
    for k, n in report["as_text"].items():
        print(f"kept as text: {k} x{n}")
    for k, n in report["unknown"].items():
        print(f"unknown event kept as text: {k} x{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
