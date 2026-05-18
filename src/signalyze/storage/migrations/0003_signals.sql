-- Phase 3 schema: structured signal payloads extracted from messages.

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    message_uid TEXT NOT NULL UNIQUE REFERENCES messages(message_uid),
    group_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    direction TEXT NOT NULL,
    instrument TEXT NOT NULL,
    entry REAL,
    entry_low REAL,
    entry_high REAL,
    stop_loss REAL,
    take_profits TEXT NOT NULL,
    quality_flag TEXT NOT NULL,
    parse_method TEXT NOT NULL,
    parse_confidence REAL NOT NULL,
    parse_version TEXT NOT NULL,
    parse_reasons TEXT,
    parsed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_group_time ON signals(group_id, timestamp_utc);
