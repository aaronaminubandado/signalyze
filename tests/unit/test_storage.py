"""Schema migration and basic repository round-trip tests."""

from __future__ import annotations

from signalyze.domain import Direction, Message, QualityFlag, Signal
from signalyze.storage import Database
from signalyze.storage.repositories import fetch_signals, upsert_messages, upsert_signal


def test_schema_creates_expected_tables(tmp_db: Database) -> None:
    cur = tmp_db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    tables = {row["name"] for row in cur}
    expected = {
        "actual_outcomes",
        "follow_ups",
        "groups",
        "market_bars",
        "message_classifications",
        "messages",
        "reported_outcomes",
        "schema_version",
        "signal_follow_up_links",
        "signals",
    }
    assert expected.issubset(tables)


def test_message_and_signal_roundtrip(tmp_db: Database) -> None:
    msg = Message(
        message_uid="g:42",
        group_id="g",
        message_id=42,
        timestamp_utc="2026-01-17T10:00:00Z",
        text="BUY XAUUSD @ 4700, SL 4690, TP 4710",
        ingested_at="2026-01-17T10:00:01Z",
        ingest_method="csv_backfill",
    )
    with tmp_db.transaction() as conn:
        upsert_messages(conn, [msg])

    signal = Signal(
        signal_id="g:42",
        message_uid="g:42",
        group_id="g",
        timestamp_utc=msg.timestamp_utc,
        direction=Direction.BUY,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0],
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.95,
        parse_version="v0.1",
        parsed_at="2026-01-17T10:00:02Z",
    )
    with tmp_db.transaction() as conn:
        upsert_signal(conn, signal)

    loaded = fetch_signals(tmp_db.conn, group_id="g")
    assert len(loaded) == 1
    assert loaded[0].entry == 4700.0
    assert loaded[0].take_profits == [4710.0]
