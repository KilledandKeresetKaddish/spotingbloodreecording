"""Flo converter on a made-up export, then imported into a throwaway database."""

import json

from fastapi.testclient import TestClient

from app.main import create_app
from tools.flo_to_healthlog import convert


def cycle(start, end, intensity=None):
    return {"period_start_date": f"{start} 00:00:00.0", "period_end_date": f"{end} 00:00:00.0", "pregnant": False,
            "period_intensity": json.dumps(intensity or {})}


def event(day, category, sub, **props):
    return {"date": f"{day} 00:00:00", "local_date": f"{day} 00:00:00", "category": category,
            "subcategory": sub, "properties": props}


EXPORT = {
    "operationalData": {
        "cycles": [cycle("2030-01-01", "2030-01-05", {"0": 3, "1": 2}), cycle("2030-01-30", "2030-02-03"),
                   cycle("2030-03-01", "2030-03-04")],
        "notes": [{"date": "2030-01-12 00:00:00.0", "text": "出血少，褐色"}],
        "repeatable_child_point_events": [
            {"date": "2030-01-02 09:00:00.0", "category": "Medication", "properties": json.dumps({"title": "药A"}),
             "deleted": False}],
    },
    "pointEventsManualData": {"point_events_manual_v2": [
        event("2030-01-02", "Temperature", "Basal", value=36.599998474121094),
        event("2030-01-02", "Weight", "Weight", value=52.29999923706055),
        event("2030-01-02", "Water", "Water", volume=0.5),
        event("2030-01-02", "Water", "Water", volume=0.5),  # the same serving twice: both count
        event("2030-01-03", "Fluid", "Bloody"),          # on a period day: nothing extra
        event("2030-01-12", "Fluid", "Bloody"),          # outside: spotting without an amount
        event("2030-01-12", "Mood", "Sad"), event("2030-01-12", "Mood", "Depressed"),
        event("2030-01-12", "Sex", "SexNone"),
        event("2030-01-13", "Mood", "ObsessiveThoughts"),
        event("2030-01-14", "Something", "New", level=2),
        event("2030-03-02", "Symptom", "Bloating"),      # after --before
    ]},
}


def by(out, day, item_id):
    return [e for e in out["entries"] if e["date"] == day and e["item_id"] == item_id]


def test_mapping():
    out, report = convert(EXPORT, before="2030-03-01")
    periods = sorted(e["date"] for e in out["entries"] if e["item_id"] == "period")
    assert periods[:5] == [f"2030-01-0{d}" for d in range(1, 6)] and len(periods) == 10
    assert by(out, "2030-01-01", "flow")[0]["value"] == 3 and by(out, "2030-01-02", "flow")[0]["value"] == 2
    assert by(out, "2030-01-02", "temperature")[0]["value"] == 36.6
    assert by(out, "2030-01-02", "weight")[0]["value"] == 52.3
    assert [e["value"] for e in by(out, "2030-01-02", "water")] == [1000]
    assert "Flo 用药：09:00 药A" in by(out, "2030-01-02", "note")[0]["text"]
    assert not by(out, "2030-01-03", "ovumcy_spotting") and by(out, "2030-01-12", "ovumcy_spotting")
    assert by(out, "2030-01-12", "mood_07")[0]["text"] == "Flo：难过；Flo：抑郁"
    assert by(out, "2030-01-12", "note")[0]["text"] == "出血少，褐色"
    assert "强迫思维" in by(out, "2030-01-13", "note")[0]["text"]
    assert "Something / New" in by(out, "2030-01-14", "note")[0]["text"]
    assert not [e for e in out["entries"] if e["date"] >= "2030-03-01"]
    assert report["skipped_nothing"] == 1 and report["dropped_after"] == 1


def test_imports_cleanly(tmp_path):
    out, _ = convert(EXPORT)
    with TestClient(create_app(str(tmp_path / "f.db"))) as client:
        r = client.post("/api/import", json=out)
        assert r.status_code == 200, r.text
        assert r.json()["entries"] == len(out["entries"])
        cycles = client.get("/api/cycles", params={"today": "2030-03-10"}).json()
        assert [c["length"] for c in cycles["cycles"][1:]] == [30, 29]
