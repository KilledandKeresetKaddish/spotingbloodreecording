"""HTTP API and the web page.

Run with:  uvicorn app.main:create_app --factory
The database path comes from HEALTH_DB (default: data/health.db).
There is no login here on purpose: put the app behind Caddy (basic auth) and
never publish the port directly.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cycles
from .db import connect, migrate
from .seed import DEFAULT_SETTINGS, seed

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DAY = timedelta(days=1)

SINGLE_KINDS = {"switch", "toggle", "level", "number", "text"}  # at most one entry per day
CUSTOM_KINDS = ("toggle", "level", "count", "number", "result", "medication", "text")
UNHIDEABLE_ROLES = {"period", "flow"}
UNDELETABLE_ROLES = {"period", "flow", "lh"}  # the cycle rules depend on these
MAX_STRIP_ITEMS = 2
MAX_RANGE_DAYS = 400
MAX_FUTURE_DAYS = 800
LEVEL_MAX = 9.9
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EXPORT_FORMAT = "health-log"
EXPORT_VERSION = 1

# key: (min, max, integer?)
SETTING_RULES: dict[str, tuple[float, float, bool]] = {
    "typical_period_length": (1, 15, True),
    "typical_cycle_length": (15, 90, True),
    "ovulation_offset_days": (7, 20, True),
    "lh_positive_threshold": (0.1, 500, False),
    "cup_ml": (10, 3000, True),
    "new_cycle_gap_days": (1, 30, True),
    "prediction_cycles": (1, 24, True),
    "outlier_cycle_days": (35, 180, True),
    "onboarded": (0, 1, True),
}


# ---------------------------------------------------------------- helpers

def bad(message: str, status: int = 400):
    raise HTTPException(status_code=status, detail=message)


def parse_date(value: Any, name: str = "date") -> date:
    if not isinstance(value, str) or not DATE_RE.match(value):
        bad(f"{name} 需要 YYYY-MM-DD 格式", 422)
    try:
        return date.fromisoformat(value)
    except ValueError:
        bad(f"{name} 不是有效的日期", 422)


def resolve_today(value: str | None) -> date:
    """The browser sends its own local date, so travel across time zones just works."""
    return parse_date(value, "today") if value else date.today()


def load_settings(conn: sqlite3.Connection) -> dict[str, int | float]:
    raw = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    out: dict[str, int | float] = {}
    for key, (_lo, _hi, is_int) in SETTING_RULES.items():
        try:
            number = float(raw.get(key, DEFAULT_SETTINGS[key]))
        except ValueError:
            number = float(DEFAULT_SETTINGS[key])
        out[key] = int(number) if is_int else number
    return out


def rules_from(settings: dict) -> cycles.Rules:
    return cycles.Rules(
        typical_cycle=settings["typical_cycle_length"],
        typical_period=settings["typical_period_length"],
        ovulation_offset=settings["ovulation_offset_days"],
        gap_days=settings["new_cycle_gap_days"],
        prediction_cycles=settings["prediction_cycles"],
        outlier_cycle=settings["outlier_cycle_days"],
    )


def item_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "category_id": row["category_id"],
        "name": row["name"],
        "kind": row["kind"],
        "role": row["role"],
        "unit": row["unit"],
        "default_value": row["default_value"],
        "options": json.loads(row["options"]) if row["options"] else None,
        "sort": row["sort"],
        "hidden": bool(row["hidden"]),
        "on_strip": bool(row["on_strip"]),
    }


def category_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "sort": row["sort"],
        "builtin": bool(row["builtin"]),
        "hidden": bool(row["hidden"]),
    }


def entry_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in ("id", "date", "item_id", "value", "choice", "text", "time", "source", "created_at")}


def get_item(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        bad("没有这个项目", 404)
    return row


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        bad("没有这条记录", 404)
    return row


def role_item_id(conn: sqlite3.Connection, role: str) -> str:
    row = conn.execute("SELECT id FROM items WHERE role = ?", (role,)).fetchone()
    if row is None:
        bad(f"缺少内置项目 {role}", 500)
    return row["id"]


def is_lh_positive(value: float | None, choice: str | None, threshold: float) -> bool:
    return choice == "阳性" or (value is not None and value >= threshold)


def period_sources(conn: sqlite3.Connection) -> dict[str, str]:
    """date -> 'manual' | 'auto' for every day with the 月经 switch on."""
    rows = conn.execute(
        "SELECT e.date, e.source FROM entries e JOIN items i ON i.id = e.item_id WHERE i.role = 'period'"
    )
    return {r["date"]: r["source"] for r in rows}


def lh_positive_dates(conn: sqlite3.Connection, threshold: float) -> list[date]:
    rows = conn.execute(
        "SELECT e.date, e.value, e.choice FROM entries e JOIN items i ON i.id = e.item_id WHERE i.role = 'lh'"
    )
    return sorted({date.fromisoformat(r["date"]) for r in rows if is_lh_positive(r["value"], r["choice"], threshold)})


def timeline_for(conn: sqlite3.Connection, settings: dict, today: date, horizon: date) -> cycles.Timeline:
    periods = [date.fromisoformat(d) for d in period_sources(conn)]
    lh = lh_positive_dates(conn, settings["lh_positive_threshold"])
    horizon = min(horizon, today + MAX_FUTURE_DAYS * DAY)
    return cycles.build_timeline(periods, lh, today, rules_from(settings), horizon)


def day_payloads(conn: sqlite3.Connection, settings: dict, start: date, end: date,
                 today: date, timeline: cycles.Timeline) -> list[dict]:
    """What the calendar and the strip need for each day from start to end (inclusive)."""
    rows = conn.execute(
        "SELECT e.date, e.item_id, e.value, e.choice, e.source, i.role, i.category_id"
        " FROM entries e JOIN items i ON i.id = e.item_id WHERE e.date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    pinned = [r["id"] for r in conn.execute(
        "SELECT id FROM items WHERE on_strip = 1 AND hidden = 0 ORDER BY sort, rowid")]
    threshold = settings["lh_positive_threshold"]
    by_date: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    out = []
    d = start
    while d <= end:
        ds = d.isoformat()
        rs = by_date.get(ds, [])
        period = next((r for r in rs if r["role"] == "period"), None)
        flows = [r["value"] for r in rs if r["role"] == "flow" and r["value"] is not None]
        other_bleeding = any(r["category_id"] == "bleeding" and r["role"] != "period" for r in rs)
        info = cycles.describe_day(d, timeline.cycles, today)
        out.append({
            "date": ds,
            **info,
            "period": period is not None,
            "period_auto": period is not None and period["source"] == "auto",
            "period_predicted": period is None and info["phase"] == "menstrual" and bool(info["predicted_cycle"]),
            "flow": max(flows) if (flows and period is not None) else None,
            # spotting = bleeding recorded with the 月经 switch off; 0 means "recorded, no amount"
            "spotting": (max(flows) if flows else 0) if (period is None and other_bleeding) else None,
            "lh_positive": any(r["role"] == "lh" and is_lh_positive(r["value"], r["choice"], threshold) for r in rs),
            "has_note": any(r["role"] == "note" for r in rs),
            "records": sum(1 for r in rs if r["role"] != "period"),
            "pins": [p for p in pinned if any(r["item_id"] == p for r in rs)],
        })
        d += DAY
    return out


def clean_entry(item: sqlite3.Row, settings: dict, value: float | None, choice: str | None,
                text: str | None, time: str | None) -> tuple:
    kind = item["kind"]
    options = json.loads(item["options"]) if item["options"] else None
    text = (text.strip() or None) if isinstance(text, str) else None
    choice = (choice.strip() or None) if isinstance(choice, str) else None
    time = (time.strip() or None) if isinstance(time, str) else None
    if time is not None and not TIME_RE.match(time):
        bad("时间需要 HH:MM 格式", 422)
    if value is not None and not math.isfinite(value):
        bad("数值无效", 422)

    if kind == "switch":
        bad("月经开关请用 PUT /api/days/{date}/period")
    if kind in ("toggle", "text"):
        value, choice = None, None
        if kind == "text" and not text:
            bad("笔记不能是空的", 422)
    elif kind == "level":
        choice = None
        if value is None:
            bad("需要一个数值", 422)
        value = round(value, 1)
        if not 0 < value <= LEVEL_MAX:
            bad(f"数值要在 0.1 到 {LEVEL_MAX} 之间", 422)
    elif kind in ("count", "medication"):
        choice = None
        if value is None:
            value = item["default_value"]
        if value is None:
            value = settings["cup_ml"] if item["role"] == "water" else 1
        if not 0 < value <= 100000:
            bad("数值要大于 0", 422)
    elif kind == "number":
        choice = None
        if value is None:
            bad("需要一个数值", 422)
    elif kind == "result":
        if choice is not None and options and choice not in options:
            bad("结果只能是：" + " / ".join(options), 422)
        if value is not None and value < 0:
            bad("数值不能是负数", 422)
        if value is None and choice is None:
            bad("需要一个读数或结果", 422)
    return value, choice, text, time


def clean_options(kind: str, options: list[str] | None) -> str | None:
    if options is None:
        return None
    if kind not in ("level", "result"):
        bad("只有等级和结果类项目可以设选项", 422)
    names = [o.strip() for o in options if isinstance(o, str) and o.strip()]
    if not 1 <= len(names) <= 10 or len(set(names)) != len(names) or any(len(n) > 20 for n in names):
        bad("选项要 1–10 个、不重复、每个不超过 20 字", 422)
    return json.dumps(names, ensure_ascii=False)


def set_period(conn: sqlite3.Connection, d: date, on: bool, settings: dict, today: date) -> list[str]:
    """Turn the 月经 switch on or off for one day. Returns the dates that changed.

    On: if the day starts a new cycle, the following days are filled in as 'auto'
    up to the predicted period length, stopping at the first day already marked.
    Off: the day is cleared, and so are the 'auto' days right after it.
    """
    rules = rules_from(settings)
    sources = period_sources(conn)
    period_id = role_item_id(conn, "period")
    ds = d.isoformat()
    changed: list[str] = []

    if on:
        if ds in sources:
            if sources[ds] == "auto":  # confirming a filled-in day
                conn.execute("UPDATE entries SET source = 'manual' WHERE item_id = ? AND date = ?", (period_id, ds))
            conn.commit()
            return changed
        conn.execute("INSERT INTO entries (date, item_id, value, source) VALUES (?, ?, 1, 'manual')", (ds, period_id))
        changed.append(ds)
        starts_cycle = not any(
            d - rules.gap_days * DAY <= date.fromisoformat(x) < d for x in sources
        )
        if starts_cycle:
            groups = cycles.group_periods([date.fromisoformat(x) for x in sources], rules.gap_days)
            length = cycles.compute_stats(groups, today, rules).period_length
            for k in range(1, length):
                dk = (d + k * DAY).isoformat()
                if dk in sources:
                    break
                conn.execute("INSERT INTO entries (date, item_id, value, source) VALUES (?, ?, 1, 'auto')",
                             (dk, period_id))
                changed.append(dk)
    else:
        if ds not in sources:
            return changed
        conn.execute("DELETE FROM entries WHERE item_id = ? AND date = ?", (period_id, ds))
        changed.append(ds)
        k = 1
        while sources.get((d + k * DAY).isoformat()) == "auto":
            dk = (d + k * DAY).isoformat()
            conn.execute("DELETE FROM entries WHERE item_id = ? AND date = ?", (period_id, dk))
            changed.append(dk)
            k += 1
    conn.commit()
    return changed


def cycle_summary(c: cycles.Cycle, today: date, outlier: int | None = None) -> dict:
    return {
        "start": c.start.isoformat(),
        "last_day": (c.end - DAY).isoformat(),
        "length": c.length,
        "complete": c.complete,
        "predicted": c.predicted,
        "current": c.start <= today < c.end and not c.predicted,
        "period_days": len(c.menstrual),
        "window_start": c.window_start.isoformat(),
        "ovulation": c.ovulation.isoformat(),
        "measured": c.measured,
        "outlier": bool(outlier) and c.complete and c.length > outlier,
    }


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        bad("名字不能是空的", 422)
    return name


def clean_settings(body: dict[str, Any]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in body.items():
        if key not in SETTING_RULES:
            bad(f"不认识的设置：{key}", 422)
        lo, hi, is_int = SETTING_RULES[key]
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            bad(f"{key} 需要一个数字", 422)
        try:
            number = float(value)
        except ValueError:
            bad(f"{key} 需要一个数字", 422)
        if not math.isfinite(number) or not lo <= number <= hi or (is_int and number != int(number)):
            bad(f"{key} 要在 {lo:g}–{hi:g} 之间" + ("的整数" if is_int else ""), 422)
        clean[key] = str(int(number)) if is_int else f"{number:g}"
    return clean


def save_settings(conn: sqlite3.Connection, clean: dict[str, str]) -> None:
    for key, value in clean.items():
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


# ---------------------------------------------------------------- request bodies

class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    hidden: bool | None = None


class OrderIn(BaseModel):
    ids: list[str]


class ItemIn(BaseModel):
    category_id: str
    name: str = Field(min_length=1, max_length=40)
    kind: str
    unit: str | None = Field(default=None, max_length=16)
    default_value: float | None = None
    options: list[str] | None = None


class ItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    category_id: str | None = None
    unit: str | None = Field(default=None, max_length=16)
    default_value: float | None = None
    options: list[str] | None = None
    hidden: bool | None = None
    on_strip: bool | None = None


class EntryIn(BaseModel):
    date: str
    item_id: str
    value: float | None = None
    choice: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, max_length=4000)
    time: str | None = Field(default=None, max_length=5)


class EntryPatch(BaseModel):
    value: float | None = None
    choice: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, max_length=4000)
    time: str | None = Field(default=None, max_length=5)


class PeriodIn(BaseModel):
    on: bool


# ---------------------------------------------------------------- app

def create_app(db_path: str | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("HEALTH_DB", "data/health.db")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            conn = connect(db_path)
        except (OSError, sqlite3.OperationalError) as exc:
            raise RuntimeError(
                f"打不开数据库 {db_path}: {exc}. 用 Docker 时先在服务器上运行: "
                "mkdir -p data && sudo chown 10001:10001 data"
            ) from exc
        try:
            migrate(conn)
            seed(conn)
        finally:
            conn.close()
        yield

    app = FastAPI(title="健康记录", lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Depends(get_conn)

    # -- basics

    @app.get("/api/health")
    def health(conn: sqlite3.Connection = Conn):
        version = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
        return {"ok": True, "schema": version}

    @app.get("/api/config")
    def config(conn: sqlite3.Connection = Conn):
        counts = dict(conn.execute("SELECT item_id, COUNT(*) FROM entries GROUP BY item_id").fetchall())
        items = [item_dict(r) for r in conn.execute("SELECT * FROM items ORDER BY sort, rowid")]
        for i in items:
            i["entries"] = counts.get(i["id"], 0)
            i["deletable"] = i["role"] not in UNDELETABLE_ROLES
        cats = []
        for row in conn.execute("SELECT * FROM categories ORDER BY sort, rowid"):
            c = category_dict(row)
            c["items"] = [i for i in items if i["category_id"] == c["id"]]
            cats.append(c)
        return {"categories": cats, "settings": load_settings(conn),
                "kinds": list(CUSTOM_KINDS), "max_strip_items": MAX_STRIP_ITEMS}

    # -- categories and items. Hiding keeps the records; deleting an item that has
    #    records needs ?with_entries=true and removes them too.

    @app.delete("/api/categories/{cat_id}")
    def delete_category(cat_id: str, conn: sqlite3.Connection = Conn):
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
        if row is None:
            bad("没有这个分类", 404)
        if row["builtin"]:
            bad("血量和检测是内置分类，不能删除")
        if conn.execute("SELECT 1 FROM items WHERE category_id = ?", (cat_id,)).fetchone():
            bad("分类里还有项目，先把它们删掉或移到别的分类", 409)
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        conn.commit()
        return category_dict(row)

    @app.delete("/api/items/{item_id}")
    def delete_item(item_id: str, with_entries: bool = False, conn: sqlite3.Connection = Conn):
        item = get_item(conn, item_id)
        if item["role"] in UNDELETABLE_ROLES:
            bad("月经、经量和排卵试纸是算周期用的，不能删除")
        count = conn.execute("SELECT COUNT(*) FROM entries WHERE item_id = ?", (item_id,)).fetchone()[0]
        if count and not with_entries:
            bad(f"这个项目有 {count} 条记录，要连记录一起删", 409)
        conn.execute("DELETE FROM entries WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
        return {**item_dict(item), "deleted_entries": count}

    @app.post("/api/categories", status_code=201)
    def add_category(body: CategoryIn, conn: sqlite3.Connection = Conn):
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM categories").fetchone()[0]
        cat_id = new_id("c")
        conn.execute("INSERT INTO categories (id, name, sort) VALUES (?, ?, ?)", (cat_id, clean_name(body.name), sort))
        conn.commit()
        return category_dict(conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone())

    @app.patch("/api/categories/{cat_id}")
    def edit_category(cat_id: str, body: CategoryPatch, conn: sqlite3.Connection = Conn):
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
        if row is None:
            bad("没有这个分类", 404)
        if body.hidden and row["builtin"]:
            bad("血量和检测是内置分类，不能隐藏")
        if body.name is not None:
            conn.execute("UPDATE categories SET name = ? WHERE id = ?", (clean_name(body.name), cat_id))
        if body.hidden is not None:
            conn.execute("UPDATE categories SET hidden = ? WHERE id = ?", (int(body.hidden), cat_id))
        conn.commit()
        return category_dict(conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone())

    @app.put("/api/categories/order")
    def order_categories(body: OrderIn, conn: sqlite3.Connection = Conn):
        known = {r["id"] for r in conn.execute("SELECT id FROM categories")}
        if set(body.ids) != known or len(body.ids) != len(known):
            bad("排序要包含全部分类，各一次", 422)
        for sort, cat_id in enumerate(body.ids):
            conn.execute("UPDATE categories SET sort = ? WHERE id = ?", (sort, cat_id))
        conn.commit()
        return {"ok": True}

    @app.put("/api/categories/{cat_id}/order")
    def order_items(cat_id: str, body: OrderIn, conn: sqlite3.Connection = Conn):
        known = {r["id"] for r in conn.execute("SELECT id FROM items WHERE category_id = ?", (cat_id,))}
        if not known or set(body.ids) != known or len(body.ids) != len(known):
            bad("排序要包含这个分类的全部项目，各一次", 422)
        for sort, item_id in enumerate(body.ids):
            conn.execute("UPDATE items SET sort = ? WHERE id = ?", (sort, item_id))
        conn.commit()
        return {"ok": True}

    @app.post("/api/items", status_code=201)
    def add_item(body: ItemIn, conn: sqlite3.Connection = Conn):
        if conn.execute("SELECT 1 FROM categories WHERE id = ?", (body.category_id,)).fetchone() is None:
            bad("没有这个分类", 404)
        if body.kind not in CUSTOM_KINDS:
            bad("类型只能是：" + ", ".join(CUSTOM_KINDS), 422)
        if body.default_value is not None and (not math.isfinite(body.default_value) or body.default_value <= 0):
            bad("默认量要大于 0", 422)
        if body.kind == "result" and not body.options:
            bad("结果类项目至少要一个选项，比如：阴性, 阳性", 422)
        options = clean_options(body.kind, body.options)
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM items WHERE category_id = ?",
                            (body.category_id,)).fetchone()[0]
        item_id = new_id("i")
        conn.execute(
            "INSERT INTO items (id, category_id, name, kind, unit, default_value, options, sort)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, body.category_id, clean_name(body.name), body.kind,
             (body.unit or "").strip() or None, body.default_value, options, sort),
        )
        conn.commit()
        return item_dict(get_item(conn, item_id))

    @app.patch("/api/items/{item_id}")
    def edit_item(item_id: str, body: ItemPatch, conn: sqlite3.Connection = Conn):
        item = get_item(conn, item_id)
        fields = body.model_fields_set
        updates: dict[str, Any] = {}
        if body.name is not None:
            updates["name"] = clean_name(body.name)
        if "category_id" in fields and body.category_id is not None and body.category_id != item["category_id"]:
            if item["role"]:
                bad("内置项目不能换分类")
            if conn.execute("SELECT 1 FROM categories WHERE id = ?", (body.category_id,)).fetchone() is None:
                bad("没有这个分类", 404)
            updates["category_id"] = body.category_id
            updates["sort"] = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM items WHERE category_id = ?",
                                           (body.category_id,)).fetchone()[0]
        if "unit" in fields:
            updates["unit"] = (body.unit or "").strip() or None
        if "default_value" in fields:
            if body.default_value is not None and (not math.isfinite(body.default_value) or body.default_value <= 0):
                bad("默认量要大于 0", 422)
            updates["default_value"] = body.default_value
        if "options" in fields:
            if item["role"]:
                bad("内置项目的选项不能改")
            if item["kind"] == "result" and not body.options:
                bad("结果类项目至少要一个选项", 422)
            updates["options"] = clean_options(item["kind"], body.options)
        if body.hidden is not None:
            if body.hidden and item["role"] in UNHIDEABLE_ROLES:
                bad("月经和经量不能隐藏")
            updates["hidden"] = int(body.hidden)
            if body.hidden:
                updates["on_strip"] = 0
        if body.on_strip is not None:
            if body.on_strip and not item["on_strip"]:
                pinned = conn.execute("SELECT COUNT(*) FROM items WHERE on_strip = 1 AND id != ?",
                                      (item_id,)).fetchone()[0]
                if pinned >= MAX_STRIP_ITEMS:
                    bad(f"最多显示 {MAX_STRIP_ITEMS} 个记号")
                if (body.hidden if body.hidden is not None else item["hidden"]):
                    bad("隐藏的项目不能显示在周期条上")
            updates["on_strip"] = int(body.on_strip)
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE items SET {sets} WHERE id = ?", (*updates.values(), item_id))
            conn.commit()
        return item_dict(get_item(conn, item_id))

    # -- entries

    @app.post("/api/entries")
    def add_entry(body: EntryIn, conn: sqlite3.Connection = Conn):
        d = parse_date(body.date)
        item = get_item(conn, body.item_id)
        value, choice, text, time = clean_entry(item, load_settings(conn), body.value, body.choice,
                                                body.text, body.time)
        existing = None
        if item["kind"] in SINGLE_KINDS:
            existing = conn.execute("SELECT id FROM entries WHERE date = ? AND item_id = ? ORDER BY id LIMIT 1",
                                    (d.isoformat(), item["id"])).fetchone()
        if existing:
            entry_id = existing["id"]
            conn.execute("UPDATE entries SET value = ?, choice = ?, text = ?, time = ?, source = 'manual'"
                         " WHERE id = ?", (value, choice, text, time, entry_id))
        else:
            cur = conn.execute("INSERT INTO entries (date, item_id, value, choice, text, time)"
                               " VALUES (?, ?, ?, ?, ?, ?)", (d.isoformat(), item["id"], value, choice, text, time))
            entry_id = cur.lastrowid
        conn.commit()
        return entry_dict(get_entry(conn, entry_id))

    @app.patch("/api/entries/{entry_id}")
    def edit_entry(entry_id: int, body: EntryPatch, conn: sqlite3.Connection = Conn):
        entry = get_entry(conn, entry_id)
        item = get_item(conn, entry["item_id"])
        merged = {k: entry[k] for k in ("value", "choice", "text", "time")}
        merged.update({k: getattr(body, k) for k in body.model_fields_set})
        value, choice, text, time = clean_entry(item, load_settings(conn), merged["value"], merged["choice"],
                                                merged["text"], merged["time"])
        conn.execute("UPDATE entries SET value = ?, choice = ?, text = ?, time = ?, source = 'manual'"
                     " WHERE id = ?", (value, choice, text, time, entry_id))
        conn.commit()
        return entry_dict(get_entry(conn, entry_id))

    @app.delete("/api/entries/{entry_id}")
    def delete_entry(entry_id: int, conn: sqlite3.Connection = Conn):
        entry = get_entry(conn, entry_id)
        if get_item(conn, entry["item_id"])["kind"] == "switch":
            bad("月经开关请用 PUT /api/days/{date}/period")
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        conn.commit()
        return entry_dict(entry)

    # -- days, calendar, cycles

    @app.get("/api/days/{day}")
    def get_day(day: str, today: str | None = None, conn: sqlite3.Connection = Conn):
        d = parse_date(day)
        t = resolve_today(today)
        settings = load_settings(conn)
        timeline = timeline_for(conn, settings, t, max(d, t))
        entries = [entry_dict(r) for r in conn.execute(
            "SELECT * FROM entries WHERE date = ? ORDER BY COALESCE(time, '99:99'), id", (d.isoformat(),))]
        summary = day_payloads(conn, settings, d, d, t, timeline)[0]
        return {"date": d.isoformat(), "entries": entries, "summary": summary}

    @app.put("/api/days/{day}/period")
    def put_period(day: str, body: PeriodIn, today: str | None = None, conn: sqlite3.Connection = Conn):
        d = parse_date(day)
        changed = set_period(conn, d, body.on, load_settings(conn), resolve_today(today))
        return {"date": d.isoformat(), "on": body.on, "changed": changed}

    @app.get("/api/calendar")
    def calendar(
        start: str = Query(alias="from"),
        end: str = Query(alias="to"),
        today: str | None = None,
        conn: sqlite3.Connection = Conn,
    ):
        a, b, t = parse_date(start, "from"), parse_date(end, "to"), resolve_today(today)
        if b < a or (b - a).days > MAX_RANGE_DAYS:
            bad(f"日期范围要在 {MAX_RANGE_DAYS} 天以内", 422)
        settings = load_settings(conn)
        timeline = timeline_for(conn, settings, t, max(b, t))
        return {
            "from": a.isoformat(), "to": b.isoformat(), "today": t.isoformat(),
            "days": day_payloads(conn, settings, a, b, t, timeline),
        }

    @app.get("/api/strip")
    def strip(day: str = Query(alias="date"), today: str | None = None, conn: sqlite3.Connection = Conn):
        """The cycle that contains a date, day by day: the strip above the calendar."""
        d, t = parse_date(day), resolve_today(today)
        settings = load_settings(conn)
        timeline = timeline_for(conn, settings, t, max(d, t) + 120 * DAY)
        c = cycles.find_cycle(timeline.cycles, d)
        if c is None:
            return {"cycle": None, "days": []}
        summary = cycle_summary(c, t, settings["outlier_cycle_days"])
        if summary["current"] and timeline.next_start:
            summary["next_start"] = timeline.next_start.isoformat()
            summary["next_range"] = [x.isoformat() for x in timeline.next_range] if timeline.next_range else None
        return {"cycle": summary, "days": day_payloads(conn, settings, c.start, c.end - DAY, t, timeline)}

    @app.get("/api/cycles")
    def cycle_list(today: str | None = None, limit: int = Query(default=12, ge=1, le=60),
                   conn: sqlite3.Connection = Conn):
        t = resolve_today(today)
        settings = load_settings(conn)
        timeline = timeline_for(conn, settings, t, t)
        recorded = [c for c in timeline.cycles if not c.predicted][-limit:]
        rows = []
        if recorded:
            days = day_payloads(conn, settings, recorded[0].start, recorded[-1].end - DAY, t, timeline)
            by_date = {x["date"]: x for x in days}
            for c in reversed(recorded):
                row = cycle_summary(c, t, settings["outlier_cycle_days"])
                row["days"] = [by_date[(c.start + k * DAY).isoformat()] for k in range(c.length)]
                rows.append(row)
        return {
            "today": t.isoformat(),
            "stats": asdict(timeline.stats) if timeline.stats else None,
            "next_start": timeline.next_start.isoformat() if timeline.next_start else None,
            "next_range": [x.isoformat() for x in timeline.next_range] if timeline.next_range else None,
            "cycles": rows,
        }

    # -- settings

    @app.get("/api/settings")
    def get_settings(conn: sqlite3.Connection = Conn):
        return load_settings(conn)

    @app.put("/api/settings")
    def put_settings(body: dict[str, Any], conn: sqlite3.Connection = Conn):
        save_settings(conn, clean_settings(body))
        conn.commit()
        return load_settings(conn)

    # -- export / import (our own format; other apps get converted into it)

    @app.get("/api/export.json")
    def export_json(conn: sqlite3.Connection = Conn):
        data = {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "settings": load_settings(conn),
            "categories": [category_dict(r) for r in conn.execute("SELECT * FROM categories ORDER BY sort, rowid")],
            "items": [item_dict(r) for r in conn.execute("SELECT * FROM items ORDER BY category_id, sort, rowid")],
            "entries": [entry_dict(r) for r in conn.execute("SELECT * FROM entries ORDER BY date, id")],
        }
        stamp = date.today().strftime("%Y%m%d")
        return Response(
            json.dumps(data, ensure_ascii=False, indent=1),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="health-{stamp}.json"'},
        )

    @app.get("/api/export.csv")
    def export_csv(conn: sqlite3.Connection = Conn):
        buf = io.StringIO()
        buf.write("﻿")  # BOM so spreadsheet apps read the Chinese text as UTF-8
        writer = csv.writer(buf)
        writer.writerow(["date", "time", "category", "item", "kind", "value", "unit", "choice", "text", "source"])
        for r in conn.execute(
            "SELECT e.*, i.name AS item_name, i.kind, i.unit, c.name AS category_name"
            " FROM entries e JOIN items i ON i.id = e.item_id JOIN categories c ON c.id = i.category_id"
            " ORDER BY e.date, COALESCE(e.time, '99:99'), e.id"
        ):
            writer.writerow([r["date"], r["time"] or "", r["category_name"], r["item_name"], r["kind"],
                             "" if r["value"] is None else f"{r['value']:g}", r["unit"] or "",
                             r["choice"] or "", r["text"] or "", r["source"]])
        stamp = date.today().strftime("%Y%m%d")
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="health-{stamp}.csv"'})

    @app.post("/api/import")
    def import_json(body: dict[str, Any], restore: bool = False, conn: sqlite3.Connection = Conn):
        """Merge an export file: new categories and items are added, identical entries skipped,
        strip markers kept while there is room. restore=true (bringing a backup back) also
        applies the file's settings, names, order and hidden flags to what already exists."""
        if body.get("format") != EXPORT_FORMAT or body.get("version") != EXPORT_VERSION:
            bad(f"需要 {EXPORT_FORMAT} v{EXPORT_VERSION} 格式的文件", 422)
        added = {"categories": 0, "items": 0, "entries": 0, "skipped": 0, "missing_item": 0}
        pins: list[str] = []  # items marked on the strip in the file, in file order
        try:
            for c in body.get("categories", []):
                cat_id, name = str(c["id"]), clean_name(str(c["name"]))[:40]
                sort, hidden = int(c.get("sort", 0)), bool(c.get("hidden"))
                row = conn.execute("SELECT builtin FROM categories WHERE id = ?", (cat_id,)).fetchone()
                if row is None:
                    conn.execute("INSERT INTO categories (id, name, sort, builtin, hidden) VALUES (?, ?, ?, 0, ?)",
                                 (cat_id, name, sort, int(hidden)))
                    added["categories"] += 1
                elif restore:
                    conn.execute("UPDATE categories SET name = ?, sort = ?, hidden = ? WHERE id = ?",
                                 (name, sort, int(hidden and not row["builtin"]), cat_id))
            for i in body.get("items", []):
                item_id, name = str(i["id"]), clean_name(str(i["name"]))[:40]
                kind, cat_id = i.get("kind"), str(i["category_id"])
                if i.get("on_strip"):
                    pins.append(item_id)
                cat_exists = conn.execute("SELECT 1 FROM categories WHERE id = ?", (cat_id,)).fetchone()
                row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
                if row is None:
                    if kind not in CUSTOM_KINDS:
                        bad(f"项目 {item_id} 的类型不对", 422)
                    if cat_exists is None:
                        bad(f"项目 {item_id} 的分类不存在", 422)
                    conn.execute(
                        "INSERT INTO items (id, category_id, name, kind, unit, default_value, options, sort, hidden)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (item_id, cat_id, name, kind, i.get("unit"), i.get("default_value"),
                         clean_options(kind, i.get("options")), int(i.get("sort", 0)), int(bool(i.get("hidden")))))
                    added["items"] += 1
                elif restore:
                    hidden = bool(i.get("hidden")) and row["role"] not in UNHIDEABLE_ROLES
                    conn.execute("UPDATE items SET name = ?, sort = ?, hidden = ?, unit = ?, default_value = ?"
                                 " WHERE id = ?", (name, int(i.get("sort", 0)), int(hidden), i.get("unit"),
                                                   i.get("default_value"), item_id))
                    if not row["role"] and kind == row["kind"]:
                        if cat_exists:
                            conn.execute("UPDATE items SET category_id = ? WHERE id = ?", (cat_id, item_id))
                        if i.get("options"):
                            conn.execute("UPDATE items SET options = ? WHERE id = ?",
                                         (clean_options(kind, i["options"]), item_id))
            if restore:
                conn.execute("UPDATE items SET on_strip = 0")
            pinned = conn.execute("SELECT COUNT(*) FROM items WHERE on_strip = 1").fetchone()[0]
            for item_id in pins:
                if pinned >= MAX_STRIP_ITEMS:
                    break
                cur = conn.execute("UPDATE items SET on_strip = 1 WHERE id = ? AND hidden = 0 AND on_strip = 0",
                                   (item_id,))
                pinned += cur.rowcount
            settings = load_settings(conn)
            if restore and isinstance(body.get("settings"), dict):
                save_settings(conn, clean_settings(
                    {k: v for k, v in body["settings"].items() if k in SETTING_RULES}))
                settings = load_settings(conn)
            for e in body.get("entries", []):
                d = parse_date(e.get("date"), "entries.date").isoformat()
                item = conn.execute("SELECT * FROM items WHERE id = ?", (str(e.get("item_id")),)).fetchone()
                if item is None:  # e.g. an item that was deleted here
                    added["missing_item"] += 1
                    continue
                source = e.get("source") if e.get("source") in ("manual", "auto") else "manual"
                if item["kind"] == "switch":
                    value, choice, text, time = 1, None, None, None
                else:
                    value, choice, text, time = clean_entry(item, settings, e.get("value"), e.get("choice"),
                                                            e.get("text"), e.get("time"))
                if item["kind"] in SINGLE_KINDS:
                    duplicate = conn.execute("SELECT 1 FROM entries WHERE date = ? AND item_id = ?",
                                             (d, item["id"])).fetchone()
                else:
                    duplicate = conn.execute(
                        "SELECT 1 FROM entries WHERE date = ? AND item_id = ? AND value IS ? AND choice IS ?"
                        " AND text IS ? AND time IS ?", (d, item["id"], value, choice, text, time)).fetchone()
                if duplicate:
                    added["skipped"] += 1
                    continue
                conn.execute("INSERT INTO entries (date, item_id, value, choice, text, time, source)"
                             " VALUES (?, ?, ?, ?, ?, ?, ?)", (d, item["id"], value, choice, text, time, source))
                added["entries"] += 1
        except (KeyError, TypeError, ValueError) as exc:
            conn.rollback()
            bad(f"文件内容不完整：{exc}", 422)
        except HTTPException:
            conn.rollback()
            raise
        conn.commit()
        return added

    # -- the page itself (mounted last so /api/* wins)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
