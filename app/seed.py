"""Default categories, items and settings, written once into an empty database."""

from __future__ import annotations

import json
import sqlite3

DEFAULT_SETTINGS: dict[str, str] = {
    "typical_period_length": "5",   # 一般经期天数
    "typical_cycle_length": "28",   # 一般周期天数
    "ovulation_offset_days": "13",  # 没有 LH 时，从预计下次月经往前推几天
    "lh_positive_threshold": "25",  # LH 数值 >= 它算阳性 (mIU/mL)
    "cup_ml": "500",                # 一杯水
    "new_cycle_gap_days": "7",      # 离上一个月经日超过这么多天，才算新周期
    "prediction_cycles": "6",       # 预测用最近几个完整周期
    "outlier_cycle_days": "60",     # 超过这么多天的周期不算进预测
    "onboarded": "0",
}

# (id, name, builtin)
CATEGORIES = [
    ("bleeding", "血量", 1),
    ("mood", "心情", 0),
    ("discharge", "分泌物", 0),
    ("symptoms", "症状", 0),
    ("life", "生活", 0),
    ("meds", "药", 0),
    ("sex", "性", 0),
    ("tests", "检测", 1),
    ("measures", "测量", 0),
    ("notes", "笔记", 0),
]

TOGGLE_LISTS = {
    "mood": ["平静", "开心", "有精神", "疲惫", "焦虑", "烦躁", "低落", "压力大", "情绪波动", "敏感"],
    "discharge": ["干", "黏稠", "奶油状", "水样", "蛋清状", "颜色异常", "异味"],
    "symptoms": ["腹痛", "绞痛", "头痛", "背痛", "失眠", "私处瘙痒", "私处干涩", "乳房胀痛",
                 "腹胀", "便秘", "腹泻", "恶心", "头晕", "疲劳", "长痘", "排卵痛"],
    "life": ["外出", "运动", "冷饮/冰淇淋", "酒", "咖啡", "熬夜", "旅行/时差", "生病", "压力事件"],
    "sex": ["性生活", "性欲", "自慰"],
}

# (id, category, name, kind, role, unit, default_value, options)
SPECIAL_ITEMS = [
    ("period", "bleeding", "月经", "switch", "period", None, None, None),
    ("flow", "bleeding", "经量", "level", "flow", None, None, ["少", "中", "多", "过多"]),
    ("clot", "bleeding", "血块", "level", "clot", None, None, ["少", "中", "多", "过多"]),
    ("color_bright", "bleeding", "鲜红", "toggle", None, None, None, None),
    ("color_dark", "bleeding", "暗红", "toggle", None, None, None, None),
    ("color_brown", "bleeding", "褐色", "toggle", None, None, None, None),
    ("color_pink", "bleeding", "粉色", "toggle", None, None, None, None),
    ("lh", "tests", "排卵试纸", "result", "lh", "mIU/mL", None, ["阴性", "阳性"]),
    ("pregnancy", "tests", "验孕", "result", "pregnancy", None, None, ["阴性", "阳性", "无效"]),
    ("temperature", "measures", "体温", "number", "temperature", "°C", None, None),
    ("weight", "measures", "体重", "number", "weight", "kg", None, None),
    ("water", "measures", "喝水", "count", "water", "ml", None, None),
    ("note", "notes", "笔记", "text", "note", None, None, None),
]


def seed(conn: sqlite3.Connection) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]:
        conn.commit()
        return

    for sort, (cat_id, name, builtin) in enumerate(CATEGORIES):
        conn.execute(
            "INSERT INTO categories (id, name, sort, builtin) VALUES (?, ?, ?, ?)",
            (cat_id, name, sort, builtin),
        )

    sort_in_cat: dict[str, int] = {}

    def next_sort(cat: str) -> int:
        sort_in_cat[cat] = sort_in_cat.get(cat, -1) + 1
        return sort_in_cat[cat]

    for item_id, cat, name, kind, role, unit, default, options in SPECIAL_ITEMS:
        conn.execute(
            "INSERT INTO items (id, category_id, name, kind, role, unit, default_value, options, sort)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, cat, name, kind, role, unit, default,
             json.dumps(options, ensure_ascii=False) if options else None, next_sort(cat)),
        )

    for cat, names in TOGGLE_LISTS.items():
        for i, name in enumerate(names, start=1):
            conn.execute(
                "INSERT INTO items (id, category_id, name, kind, sort) VALUES (?, ?, ?, 'toggle', ?)",
                (f"{cat}_{i:02d}", cat, name, next_sort(cat)),
            )
    conn.commit()
