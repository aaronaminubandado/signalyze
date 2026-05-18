-- Phase 6 schema: reported outcomes derived from each signal's linked follow-ups.

CREATE TABLE IF NOT EXISTS reported_outcomes (
    signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id),
    final_state TEXT NOT NULL,
    max_tp_hit INTEGER,
    claimed_pips REAL,
    closed_at_utc TEXT,
    source_follow_up_count INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    computed_version TEXT NOT NULL
);
