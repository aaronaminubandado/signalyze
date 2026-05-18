-- Phase 8 schema: actual outcomes derived from a walk-forward market simulation.

CREATE TABLE IF NOT EXISTS actual_outcomes (
    signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id),
    final_state TEXT NOT NULL,
    first_touch_event TEXT,
    first_touch_price REAL,
    first_touch_at_utc TEXT,
    realized_rr REAL,
    realized_pips REAL,
    bars_to_outcome INTEGER,
    win_policy TEXT NOT NULL,
    max_holding_hours REAL NOT NULL,
    default_sl_policy TEXT,
    computed_at TEXT NOT NULL,
    computed_version TEXT NOT NULL
);
