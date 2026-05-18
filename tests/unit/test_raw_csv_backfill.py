"""Raw CSV backfill: legacy CSVs round-trip into the canonical messages table."""

from __future__ import annotations

from pathlib import Path

from signalyze.ingest.raw_csv_backfill import backfill_from_csv_dir
from signalyze.storage import Database


def test_backfill_loads_legacy_csv(tmp_db: Database, tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    csv_path = raw_dir / "groupA.csv"
    csv_path.write_text(
        "group,message_id,timestamp_utc,sender_id,text,reply_to_msg_id,views,forwards,reply_count\n"
        "groupA,42,2026-01-18T10:00:00Z,sender1,BUY XAUUSD @ 4700,,100,5,\n"
        "groupA,43,2026-01-18T10:05:00Z,sender1,TP1 hit +10 pips,42,80,1,\n",
        encoding="utf-8",
    )

    stats = backfill_from_csv_dir(db=tmp_db, raw_dir=raw_dir)
    assert len(stats) == 1
    assert stats[0].rows_read == 2
    assert stats[0].rows_inserted == 2

    rows = tmp_db.conn.execute("SELECT message_id, reply_to_msg_id, text FROM messages ORDER BY message_id").fetchall()
    assert [r["message_id"] for r in rows] == [42, 43]
    assert rows[1]["reply_to_msg_id"] == 42


def test_backfill_is_idempotent(tmp_db: Database, tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "g.csv").write_text(
        "group,message_id,timestamp_utc,sender_id,text,reply_to_msg_id,views,forwards,reply_count\n"
        "g,1,2026-01-18T10:00:00Z,,hi,,,,\n",
        encoding="utf-8",
    )

    backfill_from_csv_dir(db=tmp_db, raw_dir=raw_dir)
    second = backfill_from_csv_dir(db=tmp_db, raw_dir=raw_dir)
    assert second[0].rows_read == 1
    assert second[0].rows_inserted == 0
    count = tmp_db.conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
    assert count == 1


def test_backfill_skips_unparseable_rows(tmp_db: Database, tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "g.csv").write_text(
        "group,message_id,timestamp_utc,sender_id,text,reply_to_msg_id,views,forwards,reply_count\n"
        "g,notanumber,2026-01-18T10:00:00Z,,broken,,,,\n"
        "g,5,,,no timestamp,,,,\n"
        "g,6,2026-01-18T11:00:00Z,,ok,,,,\n",
        encoding="utf-8",
    )

    [stat] = backfill_from_csv_dir(db=tmp_db, raw_dir=raw_dir)
    assert stat.rows_read == 3
    assert stat.rows_skipped == 2
    assert stat.rows_inserted == 1
