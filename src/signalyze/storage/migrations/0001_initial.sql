-- Initial Signalyze schema. All timestamps are ISO8601 UTC strings ending in 'Z'.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,
    label TEXT,
    username TEXT,
    first_seen_utc TEXT,
    last_seen_utc TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    message_uid TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    timestamp_utc TEXT NOT NULL,
    sender_id TEXT,
    text TEXT,
    reply_to_msg_id INTEGER,
    views INTEGER,
    forwards INTEGER,
    reply_count INTEGER,
    ingested_at TEXT NOT NULL,
    ingest_method TEXT NOT NULL,
    UNIQUE(group_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_group_time ON messages(group_id, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_messages_reply ON messages(group_id, reply_to_msg_id);
