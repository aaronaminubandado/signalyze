"""Schema migration and basic repository round-trip tests."""

from __future__ import annotations

from signalyze.domain import Message
from signalyze.storage import Database
from signalyze.storage.repositories import upsert_messages


def test_scaffold_schema_creates_core_tables(tmp_db: Database) -> None:
    cur = tmp_db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    tables = {row["name"] for row in cur}
    assert {"groups", "messages", "schema_version"}.issubset(tables)


def test_message_roundtrip(tmp_db: Database) -> None:
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

    row = tmp_db.conn.execute(
        "SELECT message_uid, text FROM messages WHERE message_uid = 'g:42'"
    ).fetchone()
    assert row is not None
    assert row["text"].startswith("BUY XAUUSD")
