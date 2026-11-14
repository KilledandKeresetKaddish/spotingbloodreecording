"""API behaviour against a throwaway database. All dates are made up."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

D = timedelta(days=1)
START = date(2030, 3, 1)


def iso(d: date) -> str:
    return d.isoformat()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "test.db"))) as c:
        yield c


def period_days(client, a: date, b: date, today: date) -> dict[str, bool]:
    r = client.get("/api/calendar", params={"from": iso(a), "to": iso(b), "today": iso(today)})
    assert r.status_code == 200, r.text
    return {d["date"]: d for d in r.json()["days"]}


def test_health_and_seed(client):
    assert client.get("/api/health").json()["ok"] is True
    cfg = client.get("/api/config").json()
    names = [c["name"] for c in cfg["categories"]]
    assert names[0] == "血量" and "检测" in names and len(names) == 10
    builtin = {c["id"] for c in cfg["categories"] if c["builtin"]}
    assert builtin == {"bleeding", "tests"}
    assert cfg["settings"]["typical_period_length"] == 5
    assert client.get("/").status_code == 200


def test_period_start_fills_typical_length_and_off_removes_auto_days(client):
    today = START + 10 * D
    r = client.put(f"/api/days/{iso(START)}/period", json={"on": True}, params={"today": iso(today)})
    assert r.json()["changed"] == [iso(START + k * D) for k in range(5)]
    days = period_days(client, START, START + 6 * D, today)
    assert [days[iso(START + k * D)]["period"] for k in range(6)] == [True] * 5 + [False]
    assert days[iso(START)]["period_auto"] is False and days[iso(START + D)]["period_auto"] is True

    # period ended after 3 days: switching off day 4 clears day 4 and the filled-in day 5
    r = client.put(f"/api/days/{iso(START + 3 * D)}/period", json={"on": False}, params={"today": iso(today)})
    assert r.json()["changed"] == [iso(START + 3 * D), iso(START + 4 * D)]

    # confirming a filled-in day keeps it and makes it manual
    client.put(f"/api/days/{iso(START + D)}/period", json={"on": True}, params={"today": iso(today)})
    assert period_days(client, START, START, today)[iso(START)]["period"] is True
    assert period_days(client, START + D, START + D, today)[iso(START + D)]["period_auto"] is False

    # switching off the first day clears the auto days after it but keeps manual ones
    r = client.put(f"/api/days/{iso(START)}/period", json={"on": False}, params={"today": iso(today)})
    assert r.json()["changed"] == [iso(START)]


def test_day_inside_the_gap_does_not_start_a_cycle(client):
    today = START + 20 * D
    client.put(f"/api/days/{iso(START)}/period", json={"on": True}, params={"today": iso(today)})
    r = client.put(f"/api/days/{iso(START + 9 * D)}/period", json={"on": True}, params={"today": iso(today)})
    assert r.json()["changed"] == [iso(START + 9 * D)]  # 9 - 4 = 5 days after the last period day


def test_toggle_is_one_per_day_and_can_carry_text(client):
    a = client.post("/api/entries", json={"date": iso(START), "item_id": "mood_01"}).json()
    b = client.post("/api/entries", json={"date": iso(START), "item_id": "mood_01", "text": "  下午才好一点 "}).json()
    assert a["id"] == b["id"] and b["text"] == "下午才好一点"
    day = client.get(f"/api/days/{iso(START)}").json()
    assert len(day["entries"]) == 1
    assert client.delete(f"/api/entries/{a['id']}").status_code == 200
    assert client.get(f"/api/days/{iso(START)}").json()["entries"] == []


def test_water_appends_with_cup_default(client):
    client.post("/api/entries", json={"date": iso(START), "item_id": "water"})
    client.post("/api/entries", json={"date": iso(START), "item_id": "water", "value": 300})
    values = sorted(e["value"] for e in client.get(f"/api/days/{iso(START)}").json()["entries"])
    assert values == [300, 500]


def test_level_validation_and_rounding(client):
    ok = client.post("/api/entries", json={"date": iso(START), "item_id": "flow", "value": 2.46})
    assert ok.status_code == 200 and ok.json()["value"] == 2.5
    assert client.post("/api/entries", json={"date": iso(START), "item_id": "flow", "value": 0}).status_code == 422
    assert client.post("/api/entries", json={"date": iso(START), "item_id": "flow"}).status_code == 422


def test_period_switch_is_not_an_entry(client):
    r = client.post("/api/entries", json={"date": iso(START), "item_id": "period"})
    assert r.status_code == 400


def test_lh_result_and_threshold(client):
    bad = client.post("/api/entries", json={"date": iso(START), "item_id": "lh", "choice": "强阳"})
    assert bad.status_code == 422
    client.post("/api/entries", json={"date": iso(START), "item_id": "lh", "value": 12, "time": "10:30"})
    client.post("/api/entries", json={"date": iso(START + D), "item_id": "lh", "value": 40, "time": "14:00"})
    client.post("/api/entries", json={"date": iso(START + 2 * D), "item_id": "lh", "choice": "阳性"})
    days = period_days(client, START, START + 2 * D, START + 3 * D)
    assert [days[iso(START + k * D)]["lh_positive"] for k in range(3)] == [False, True, True]
    client.put("/api/settings", json={"lh_positive_threshold": 45})
    assert period_days(client, START + D, START + D, START + 3 * D)[iso(START + D)]["lh_positive"] is False
    assert client.post("/api/entries", json={"date": iso(START), "item_id": "lh", "time": "25:00",
                                             "value": 3}).status_code == 422


def test_spotting_is_bleeding_with_the_switch_off(client):
    client.post("/api/entries", json={"date": iso(START), "item_id": "flow", "value": 1})
    client.post("/api/entries", json={"date": iso(START + D), "item_id": "color_brown"})
    days = period_days(client, START, START + 2 * D, START + 3 * D)
    assert days[iso(START)]["spotting"] == 1
    assert days[iso(START + D)]["spotting"] == 0
    assert days[iso(START + 2 * D)]["spotting"] is None


def test_lh_positive_places_the_fertile_window(client):
    today = START + 20 * D
    client.put(f"/api/days/{iso(START)}/period", json={"on": True}, params={"today": iso(today)})
    positive = START + 13 * D
    client.post("/api/entries", json={"date": iso(positive), "item_id": "lh", "value": 60})
    days = period_days(client, START, START + 27 * D, today)
    fertile = [d for d, x in days.items() if x["phase"] == "fertile"]
    assert fertile == [iso(positive + k * D) for k in range(-4, 2)]
    assert days[iso(positive + D)]["ovulation"] is True
    assert days[iso(positive)]["estimated"] is False


def test_strip_and_cycles(client):
    today = START + 70 * D
    for offset in (0, 30, 61):
        client.put(f"/api/days/{iso(START + offset * D)}/period", json={"on": True}, params={"today": iso(today)})
    strip = client.get("/api/strip", params={"date": iso(today), "today": iso(today)}).json()
    assert strip["cycle"]["current"] is True and strip["cycle"]["start"] == iso(START + 61 * D)
    assert strip["cycle"]["next_start"] == iso(START + 92 * D)  # average of 30 and 31 = 30.5 -> 31
    assert len(strip["days"]) == strip["cycle"]["length"]

    data = client.get("/api/cycles", params={"today": iso(today)}).json()
    assert data["stats"]["from_history"] is True
    assert [c["length"] for c in data["cycles"][1:]] == [31, 30]
    assert data["cycles"][0]["current"] is True
    assert len(data["cycles"][2]["days"]) == 30

    none = client.get("/api/strip", params={"date": iso(START - 5 * D), "today": iso(today)}).json()
    assert none["cycle"] is None


def test_settings_validation(client):
    assert client.put("/api/settings", json={"typical_cycle_length": 3}).status_code == 422
    assert client.put("/api/settings", json={"nope": 1}).status_code == 422
    assert client.put("/api/settings", json={"cup_ml": 2.5}).status_code == 422
    r = client.put("/api/settings", json={"cup_ml": 350, "lh_positive_threshold": "22.5"})
    assert r.json()["cup_ml"] == 350 and r.json()["lh_positive_threshold"] == 22.5


def test_categories_and_items_hide_but_never_delete(client):
    assert client.patch("/api/categories/bleeding", json={"hidden": True}).status_code == 400
    assert client.patch("/api/categories/bleeding", json={"name": "出血"}).json()["name"] == "出血"
    cat = client.post("/api/categories", json={"name": "皮肤"}).json()
    assert client.patch(f"/api/categories/{cat['id']}", json={"hidden": True}).json()["hidden"] is True

    med = client.post("/api/items", json={"category_id": "meds", "name": "药物A", "kind": "medication",
                                          "unit": "片", "default_value": 1}).json()
    entry = client.post("/api/entries", json={"date": iso(START), "item_id": med["id"], "time": "08:15"}).json()
    assert entry["value"] == 1 and entry["time"] == "08:15"
    assert client.patch(f"/api/items/{med['id']}", json={"hidden": True}).json()["hidden"] is True

    assert client.patch("/api/items/period", json={"hidden": True}).status_code == 400
    assert client.patch("/api/items/flow", json={"hidden": True}).status_code == 400
    assert client.post("/api/items", json={"category_id": "life", "name": "x", "kind": "switch"}).status_code == 422
    assert client.post("/api/items", json={"category_id": "life", "name": "  ", "kind": "toggle"}).status_code == 422


def test_at_most_two_items_on_the_strip(client):
    assert client.patch("/api/items/life_01", json={"on_strip": True}).status_code == 200
    assert client.patch("/api/items/life_04", json={"on_strip": True}).status_code == 200
    assert client.patch("/api/items/life_05", json={"on_strip": True}).status_code == 400
    client.post("/api/entries", json={"date": iso(START), "item_id": "life_01"})
    day = period_days(client, START, START, START)[iso(START)]
    assert day["pins"] == ["life_01"]


def test_reorder(client):
    ids = [c["id"] for c in client.get("/api/config").json()["categories"]]
    assert client.put("/api/categories/order", json={"ids": ids[::-1]}).status_code == 200
    assert [c["id"] for c in client.get("/api/config").json()["categories"]] == ids[::-1]
    assert client.put("/api/categories/order", json={"ids": ids[:3]}).status_code == 422


def test_export_then_import_into_a_new_database(client, tmp_path):
    today = START + 10 * D
    client.put(f"/api/days/{iso(START)}/period", json={"on": True}, params={"today": iso(today)})
    med = client.post("/api/items", json={"category_id": "meds", "name": "药物B", "kind": "medication"}).json()
    client.post("/api/entries", json={"date": iso(START), "item_id": med["id"], "value": 2})
    client.post("/api/entries", json={"date": iso(START), "item_id": "note", "text": "测试笔记"})
    dump = client.get("/api/export.json").json()
    assert dump["format"] == "health-log" and len(dump["entries"]) == 7

    csv_text = client.get("/api/export.csv").text
    assert csv_text.startswith("﻿date,") and "测试笔记" in csv_text

    with TestClient(create_app(str(tmp_path / "other.db"))) as other:
        first = other.post("/api/import", json=dump).json()
        assert first["entries"] == 7 and first["items"] == 1
        again = other.post("/api/import", json=dump).json()
        assert again["entries"] == 0 and again["skipped"] == 7
        days = period_days(other, START, START + 5 * D, today)
        assert sum(x["period"] for x in days.values()) == 5

    assert client.post("/api/import", json={"format": "other"}).status_code == 422


def test_result_items_need_options(client):
    no_options = {"category_id": "tests", "name": "试纸X", "kind": "result"}
    assert client.post("/api/items", json=no_options).status_code == 422
    assert client.post("/api/items", json={**no_options, "options": [" ", ""]}).status_code == 422
    item = client.post("/api/items", json={**no_options, "options": ["阴性", "阳性"]}).json()
    entry = client.post("/api/entries", json={"date": iso(START), "item_id": item["id"], "choice": "阳性"})
    assert entry.status_code == 200 and entry.json()["choice"] == "阳性"
    assert client.patch(f"/api/items/{item['id']}", json={"options": None}).status_code == 422


def test_import_keeps_markers_and_restore_brings_back_layout(client, tmp_path):
    med = client.post("/api/items", json={"category_id": "meds", "name": "药物A", "kind": "medication"}).json()
    client.patch(f"/api/items/{med['id']}", json={"on_strip": True})
    client.patch("/api/items/life_01", json={"on_strip": True})
    client.patch("/api/categories/bleeding", json={"name": "出血"})
    client.patch("/api/items/mood_02", json={"hidden": True})
    client.put("/api/settings", json={"cup_ml": 330})
    dump = client.get("/api/export.json").json()

    def config(c):
        cfg = c.get("/api/config").json()
        items = {i["id"]: i for cat in cfg["categories"] for i in cat["items"]}
        return cfg, items

    with TestClient(create_app(str(tmp_path / "merge.db"))) as other:
        other.post("/api/import", json=dump)
        cfg, items = config(other)
        assert items[med["id"]]["on_strip"] and items["life_01"]["on_strip"]
        assert cfg["categories"][0]["name"] == "血量"  # a plain merge leaves existing names alone
        assert cfg["settings"]["cup_ml"] == 500

    with TestClient(create_app(str(tmp_path / "restore.db"))) as other:
        other.post("/api/import", json=dump, params={"restore": "true"})
        cfg, items = config(other)
        assert cfg["categories"][0]["name"] == "出血"
        assert items["mood_02"]["hidden"] is True
        assert items[med["id"]]["on_strip"] and items["life_01"]["on_strip"]
        assert cfg["settings"]["cup_ml"] == 330


def test_delete_items_and_categories(client):
    cat = client.post("/api/categories", json={"name": "试试"}).json()
    unused = client.post("/api/items", json={"category_id": cat["id"], "name": "建错了", "kind": "toggle"}).json()
    used = client.post("/api/items", json={"category_id": cat["id"], "name": "用过的", "kind": "toggle"}).json()
    client.post("/api/entries", json={"date": iso(START), "item_id": used["id"]})
    client.post("/api/entries", json={"date": iso(START + D), "item_id": used["id"]})

    items = {i["id"]: i for c in client.get("/api/config").json()["categories"] for i in c["items"]}
    assert items[used["id"]]["entries"] == 2 and items[unused["id"]]["entries"] == 0
    assert items["period"]["deletable"] is False and items["mood_01"]["deletable"] is True

    assert client.delete(f"/api/items/{unused['id']}").status_code == 200
    assert client.delete(f"/api/categories/{cat['id']}").status_code == 409  # still holds an item
    assert client.delete(f"/api/items/{used['id']}").status_code == 409      # has records
    r = client.delete(f"/api/items/{used['id']}", params={"with_entries": "true"})
    assert r.status_code == 200 and r.json()["deleted_entries"] == 2
    assert client.get(f"/api/days/{iso(START)}").json()["entries"] == []
    assert client.delete(f"/api/categories/{cat['id']}").status_code == 200
    assert client.delete("/api/categories/bleeding").status_code == 400
    for role_item in ("period", "flow", "lh"):
        assert client.delete(f"/api/items/{role_item}").status_code == 400


def test_import_skips_entries_of_deleted_items(client):
    client.post("/api/entries", json={"date": iso(START), "item_id": "mood_01"})
    client.post("/api/entries", json={"date": iso(START), "item_id": "mood_02"})
    dump = client.get("/api/export.json").json()
    client.delete("/api/items/mood_02", params={"with_entries": "true"})
    client.delete("/api/entries/1")
    r = client.post("/api/import", json={**dump, "items": [i for i in dump["items"] if i["id"] != "mood_02"]}).json()
    assert r["entries"] == 1 and r["missing_item"] == 1

def test_outlier_cycles_are_marked(client):
    today = START + 200 * D
    for offset in (0, 30, 136, 168):
        client.put(f"/api/days/{iso(START + offset * D)}/period", json={"on": True}, params={"today": iso(today)})
    data = client.get("/api/cycles", params={"today": iso(today)}).json()
    assert [c["outlier"] for c in data["cycles"]] == [False, False, True, False]
    assert data["stats"]["excluded_cycles"] == 1 and data["stats"]["cycle_length"] == 31
    assert client.put("/api/settings", json={"outlier_cycle_days": 10}).status_code == 422
