-- Phase 5 schema: link follow-up events back to their parent signals.

CREATE TABLE IF NOT EXISTS signal_follow_up_links (
    link_id TEXT PRIMARY KEY,
    follow_up_id TEXT NOT NULL REFERENCES follow_ups(follow_up_id),
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    link_method TEXT NOT NULL,
    link_confidence REAL NOT NULL,
    reasons TEXT,
    linked_at TEXT NOT NULL,
    linker_version TEXT NOT NULL,
    UNIQUE(follow_up_id, signal_id)
);

CREATE INDEX IF NOT EXISTS idx_links_signal ON signal_follow_up_links(signal_id);
CREATE INDEX IF NOT EXISTS idx_links_follow_up ON signal_follow_up_links(follow_up_id);
