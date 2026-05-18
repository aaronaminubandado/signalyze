"""Telethon-backed Telegram fetcher. Writes messages to the canonical store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from signalyze.domain import Message
from signalyze.ingest.groups_loader import GroupTarget, coerce_entity_target
from signalyze.storage import Database
from signalyze.storage.repositories import upsert_messages
from signalyze.utils.logging import get_logger
from signalyze.utils.time import format_utc, now_utc_iso, to_utc

logger = get_logger("signalyze.ingest.telegram_fetcher")


@dataclass
class FetchStats:
    """Per-group fetch outcome."""

    label: str
    target: str
    group_id: str | None = None
    fetched_messages: int = 0
    inserted_messages: int = 0
    textless_messages: int = 0
    status: str = "ok"
    error: str | None = None


def fetch_messages_for_groups(
    *,
    client: Any,
    db: Database,
    group_targets: list[GroupTarget],
    date_from: datetime,
    date_to: datetime,
    parquet_dir: Path | None = None,
) -> list[FetchStats]:
    """Resolve groups, fetch messages in [date_from, date_to], persist to DB and optional parquet.

    The Telethon client must already be authenticated. Errors per group are recorded
    in `FetchStats` rather than raised, so one bad group does not abort the run.
    """
    from telethon.errors import RPCError  # local import keeps Telethon optional
    from telethon.utils import get_peer_id

    from_utc = to_utc(date_from)
    to_utc_dt = to_utc(date_to)
    if from_utc >= to_utc_dt:
        raise ValueError("date_from must be earlier than date_to")

    logger.info("Loading dialogs before entity resolution...")
    client.get_dialogs()

    stats: list[FetchStats] = []
    seen_group_ids: set[str] = set()

    for target in group_targets:
        stat = FetchStats(label=target.label, target=target.target)
        try:
            entity = client.get_entity(coerce_entity_target(target.target))
        except (ValueError, RPCError) as exc:
            stat.status = "skipped"
            stat.error = str(exc)
            logger.error("Skip '%s' (%s): %s", target.label, target.target, exc)
            stats.append(stat)
            continue
        except Exception as exc:  # defensive catch for inaccessible dialogs
            stat.status = "skipped"
            stat.error = str(exc)
            logger.error("Skip '%s' (%s) unexpected: %s", target.label, target.target, exc)
            stats.append(stat)
            continue

        group_id = str(get_peer_id(entity))
        stat.group_id = group_id

        if group_id in seen_group_ids:
            stat.status = "skipped"
            stat.error = "duplicate resolved group id"
            stats.append(stat)
            continue
        seen_group_ids.add(group_id)

        messages, textless = _iter_group_messages(
            client=client,
            entity=entity,
            group_id=group_id,
            date_from=from_utc,
            date_to=to_utc_dt,
        )
        stat.fetched_messages = len(messages)
        stat.textless_messages = textless

        with db.transaction() as conn:
            inserted = upsert_messages(conn, messages)
        stat.inserted_messages = inserted

        if parquet_dir is not None:
            _write_parquet(parquet_dir, group_id, messages)

        logger.info(
            "Fetched %d msgs (%d new, %d textless) for group %s",
            stat.fetched_messages,
            inserted,
            textless,
            group_id,
        )
        stats.append(stat)

    return stats


def _iter_group_messages(
    *,
    client: Any,
    entity: Any,
    group_id: str,
    date_from: datetime,
    date_to: datetime,
) -> tuple[list[Message], int]:
    messages: list[Message] = []
    textless = 0
    ingested_at = now_utc_iso()

    for message in client.iter_messages(entity, offset_date=date_to, reverse=False, limit=None):
        message_date = to_utc(message.date)
        if message_date < date_from:
            break
        if message_date > date_to:
            continue

        text_value = (message.message or "").strip()
        if not text_value:
            textless += 1

        messages.append(
            Message(
                message_uid=Message.make_uid(group_id, message.id),
                group_id=group_id,
                message_id=int(message.id),
                timestamp_utc=format_utc(message_date),
                sender_id=str(message.sender_id) if message.sender_id is not None else None,
                text=text_value,
                reply_to_msg_id=message.reply_to_msg_id,
                views=message.views,
                forwards=message.forwards,
                reply_count=_extract_reply_count(message),
                ingested_at=ingested_at,
                ingest_method="telethon",
            )
        )

    messages.sort(key=lambda m: (m.timestamp_utc, m.message_id))
    return messages, textless


def _extract_reply_count(message: Any) -> int | None:
    replies = getattr(message, "replies", None)
    if replies is None:
        return None
    return getattr(replies, "replies", None)


def _write_parquet(parquet_dir: Path, group_id: str, messages: list[Message]) -> None:
    """Best-effort raw Parquet snapshot. Idempotent: overwrites the per-group file."""
    if not messages:
        return
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        logger.warning("pandas not installed; skipping parquet write")
        return

    parquet_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([m.model_dump() for m in messages])
    out = parquet_dir / f"{group_id}.parquet"
    df.to_parquet(out, index=False)
    logger.debug("Wrote parquet snapshot: %s (%d rows)", out, len(df))
