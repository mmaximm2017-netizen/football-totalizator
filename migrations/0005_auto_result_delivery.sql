-- Additive: no changes to match/scoring or existing user push tables.
CREATE TABLE IF NOT EXISTS auto_result_checks (
    match_id INTEGER PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    identity TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auto_result_notifications (
    event_key TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    queued_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS auto_result_monitor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled BOOLEAN NOT NULL,
    enabled_since TIMESTAMPTZ NOT NULL
);
INSERT INTO auto_result_monitor (id, enabled, enabled_since)
VALUES (1, true, CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING;
