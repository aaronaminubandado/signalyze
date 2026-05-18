"""Backfill the canonical `messages` table from existing `data/raw/*.csv` snapshots.

The previous prototype emitted one CSV per group with this header:
    group,message_id,timestamp_utc,sender_id,text,reply_to_msg_id,views,forwards,reply_count

This loader is one-shot and idempotent: re-running it is safe because messages are
upserted by `(group_id, message_id)`. No Telethon dependency.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from signalyze.domain import Message
from signalyze.storage import Database
from signalyze.storage.repositories import upsert_messages
from signalyze.utils.logging import get_logger
from signalyze.utils.time import now_utc_iso

logger = get_logger("signalyze.ingest.raw_csv_backfill")


@dataclass
class BackfillStats:
    """Per-file backfill outcome."""

    group_id: str
    source_file: Path
    rows_read: int = 0
    rows_skipped: int = 0
    rows_inserted: int = 0


def backfill_from_csv_dir(
    *,
    db: Database,
    raw_dir: Path,
    parquet_dir: Path | None = None,
) -> list[BackfillStats]:
    """Load every `*.csv` in `raw_dir` into the canonical store."""
    if not raw_dir.exists():
        logger.warning("Raw dir does not exist: %s", raw_dir)
        return []

    stats: list[BackfillStats] = []
    for csv_path in sorted(raw_dir.glob("*.csv")):
        stat = _backfill_one_file(db=db, csv_path=csv_path, parquet_dir=parquet_dir)
        stats.append(stat)
        logger.info(
            "Backfilled %s: read=%d inserted=%d skipped=%d",
            csv_path.name,
            stat.rows_read,
            stat.rows_inserted,
            stat.rows_skipped,
        )
    return stats


def _backfill_one_file(
    *,
    db: Database,
    csv_path: Path,
    parquet_dir: Path | None,
) -> BackfillStats:
    group_id = csv_path.stem
    stat = BackfillStats(group_id=group_id, source_file=csv_path)
    ingested_at = now_utc_iso()
    messages: list[Message] = []

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            stat.rows_read += 1
            message = _row_to_message(row, ingested_at=ingested_at, fallback_group=group_id)
            if message is None:
                stat.rows_skipped += 1
                continue
            messages.append(message)

    if not messages:
        return stat

    with db.transaction() as conn:
        stat.rows_inserted = upsert_messages(conn, messages)

    if parquet_dir is not None:
        _write_parquet(parquet_dir, group_id, messages)

    return stat


def _row_to_message(
    row: dict[str, str],
    *,
    ingested_at: str,
    fallback_group: str,
) -> Message | None:
    """Best-effort conversion from a raw CSV row to a `Message` domain object."""
    try:
        message_id = int(float((row.get("message_id") or "").strip()))
    except (TypeError, ValueError):
        return None

    group_value = (row.get("group") or fallback_group).strip() or fallback_group
    timestamp = (row.get("timestamp_utc") or "").strip()
    if not timestamp:
        return None

    return Message(
        message_uid=Message.make_uid(group_value, message_id),
        group_id=group_value,
        message_id=message_id,
        timestamp_utc=timestamp,
        sender_id=(row.get("sender_id") or None) or None,
        text=row.get("text") or "",
        reply_to_msg_id=_optional_int(row.get("reply_to_msg_id")),
        views=_optional_int(row.get("views")),
        forwards=_optional_int(row.get("forwards")),
        reply_count=_optional_int(row.get("reply_count")),
        ingested_at=ingested_at,
        ingest_method="csv_backfill",
    )


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _write_parquet(parquet_dir: Path, group_id: str, messages: list[Message]) -> None:
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        return
    parquet_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([m.model_dump() for m in messages])
    df.to_parquet(parquet_dir / f"{group_id}.parquet", index=False)
