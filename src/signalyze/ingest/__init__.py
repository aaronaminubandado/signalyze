"""Ingestion: Telegram fetcher + raw CSV backfill into the canonical store."""

from signalyze.ingest.groups_loader import (
    GroupTarget,
    build_label_map,
    parse_groups_file,
    resolve_group_label,
)
from signalyze.ingest.raw_csv_backfill import BackfillStats, backfill_from_csv_dir
from signalyze.ingest.telegram_fetcher import FetchStats, fetch_messages_for_groups

__all__ = [
    "BackfillStats",
    "FetchStats",
    "GroupTarget",
    "backfill_from_csv_dir",
    "build_label_map",
    "fetch_messages_for_groups",
    "parse_groups_file",
    "resolve_group_label",
]
