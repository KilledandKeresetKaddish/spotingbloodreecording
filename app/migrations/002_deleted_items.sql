-- 002: remember deleted items, so importing an older export does not bring them back
CREATE TABLE deleted_items (
    id          TEXT PRIMARY KEY,
    deleted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
