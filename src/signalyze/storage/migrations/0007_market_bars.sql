-- Phase 7 schema: OHLCV bars used to evaluate signals against actual market data.

CREATE TABLE IF NOT EXISTS market_bars (
    bar_id TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    interval TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(instrument, interval, timestamp_utc, provider)
);

CREATE INDEX IF NOT EXISTS idx_bars_lookup ON market_bars(instrument, interval, timestamp_utc);
