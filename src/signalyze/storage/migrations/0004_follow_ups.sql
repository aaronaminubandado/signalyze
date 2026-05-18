-- Phase 4 schema: structured follow-up events extracted from messages.

CREATE TABLE IF NOT EXISTS follow_ups (
    follow_up_id TEXT PRIMARY KEY,
    message_uid TEXT NOT NULL UNIQUE REFERENCES messages(message_uid),
    group_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    event_type TEXT NOT NULL,
    tp_index INTEGER,
    claimed_price REAL,
    claimed_pips REAL,
    new_stop_loss REAL,
    parse_method TEXT NOT NULL,
    parse_confidence REAL NOT NULL,
    parse_version TEXT NOT NULL,
    parse_reasons TEXT,
    parsed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_follow_ups_group_time ON follow_ups(group_id, timestamp_utc);
