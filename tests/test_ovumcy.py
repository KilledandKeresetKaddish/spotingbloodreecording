"""Ovumcy converter on a made-up export, then imported into a throwaway database."""

from fastapi.testclient import TestClient

from app.main import create_app
from tools.ovumcy_to_healthlog import convert


def day(date, **kw):
    base = {"date": date, "period": False, "cycle_start": False, "is_uncertain": False, "flow": "none",
            "mood_rating": 0, "sex_activity": "none", "cervical_mucus": "none", "pregnancy_test": "none",
            "cycle_factors": [], "symptoms": {"spotting": False, "headache": False, "food_cravings": False},
            "other_symptoms": [], "notes": ""}
    symptoms = kw.pop("symptoms", {})
    base.update(kw)
    base["symptoms"] = {**base["symptoms"], **symptoms}
    return base


EXPORT = {"entries": [
    day("2030-01-01", period=True, cycle_start=True),
    day("2030-01-02", period=True, flow="heavy"),
    day("2030-01-03", period=True, flow="spotting"),
    day("2030-01-15", notes="Spotting:2\n散步", symptoms={"headache": True}),
    day("2030-01-16", symptoms={"spotting": True}),
    day("2030-01-17", mood_rating=4, notes="外出/冰淇淋", symptoms={"food_cravings": True},
        cycle_factors=["sleep_disruption", "travel"]),
    day("2030-01-20"),  # nothing recorded
    day("2030-01-30", period=True),  # Ovumcy did not mark it as a start; the 7-day gap rule does
]}


def by(out, date, item_id):
    return [e for e in out["entries"] if e["date"] == date and e["item_id"] == item_id]


def test_mapping():
    out, report = convert(EXPORT)
    assert out["format"] == "health-log" and out["version"] == 1
    assert by(out, "2030-01-02", "flow")[0]["value"] == 3
    assert by(out, "2030-01-03", "flow")[0]["value"] == 0.5
    assert by(out, "2030-01-15", "flow")[0]["value"] == 2
    assert by(out, "2030-01-15", "symptoms_03")
    assert by(out, "2030-01-15", "note")[0]["text"] == "散步"
    assert by(out, "2030-01-16", "ovumcy_spotting") and out["items"][0]["hidden"] is True
    assert by(out, "2030-01-17", "life_01") and by(out, "2030-01-17", "life_03")
    assert by(out, "2030-01-17", "life_06")[0]["text"] == "睡眠被打乱"
    note = by(out, "2030-01-17", "note")[0]["text"]
    assert note.startswith("外出/冰淇淋") and "症状：嘴馋" in note and "周期因素：travel" in note
    assert "Ovumcy 心情评分：4" in note
    assert not [e for e in out["entries"] if e["date"] == "2030-01-20"]
    assert report["unmeasured_spotting"] == ["2030-01-16"]


def test_imports_cleanly(tmp_path):
    out, _ = convert(EXPORT)
    with TestClient(create_app(str(tmp_path / "o.db"))) as client:
        r = client.post("/api/import", json=out)
        assert r.status_code == 200, r.text
        assert r.json()["entries"] == len(out["entries"])
        days = {d["date"]: d for d in client.get("/api/calendar", params={
            "from": "2030-01-01", "to": "2030-01-31", "today": "2030-02-05"}).json()["days"]}
        assert days["2030-01-15"]["spotting"] == 2
        assert days["2030-01-16"]["spotting"] == 0
        assert days["2030-01-30"]["period"] and days["2030-01-30"]["cycle_day"] == 1
        assert client.post("/api/import", json=out).json()["entries"] == 0  # second time: all duplicates
