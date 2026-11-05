-- 001: categories, items, entries, settings
-- Dates are the user's local calendar date (YYYY-MM-DD), never converted to UTC.
-- Times are local wall-clock times (HH:MM).

CREATE TABLE categories (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    sort     INTEGER NOT NULL DEFAULT 0,
    builtin  INTEGER NOT NULL DEFAULT 0,
    hidden   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE items (
    id            TEXT PRIMARY KEY,
    category_id   TEXT NOT NULL REFERENCES categories(id),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('switch', 'toggle', 'level', 'count', 'number', 'result', 'medication', 'text')),
    role          TEXT,              -- built-in meaning: period, flow, clot, lh, pregnancy, temperature, weight, water, note
    unit          TEXT,
    default_value REAL,
    options       TEXT,              -- JSON array: level names (少/中/多/过多) or result labels (阴性/阳性)
    sort          INTEGER NOT NULL DEFAULT 0,
    hidden        INTEGER NOT NULL DEFAULT 0,
    on_strip      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    item_id     TEXT NOT NULL REFERENCES items(id),
    value       REAL,              -- level, count (ml), number, medication amount, LH reading
    choice      TEXT,              -- result kinds: one of the item's options
    text        TEXT,              -- note or custom text
    time        TEXT,
    source      TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'auto')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_entries_date ON entries(date);
CREATE INDEX idx_entries_item_date ON entries(item_id, date);

CREATE TABLE settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
