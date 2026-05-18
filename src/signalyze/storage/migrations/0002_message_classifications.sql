-- Phase 2 schema: per-message classification outcomes.

CREATE TABLE IF NOT EXISTS message_classifications (
    message_uid TEXT PRIMARY KEY REFERENCES messages(message_uid),
    class TEXT NOT NULL,
    confidence REAL NOT NULL,
    method TEXT NOT NULL,
    reasons TEXT,
    classifier_version TEXT NOT NULL,
    classified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_classifications_class ON message_classifications(class);
