"""Compute the market-data coverage required by all signals and fetch it.

The runner:
  1. Inspects the signals table to determine the smallest contiguous time range
     needed (per instrument).
  2. Detects which segments are already cached in `market_bars`.
  3. Asks the provider to fill the missing segments and persists them.

Idempotent: calling `fetch_required_bars` twice in a row yields zero fetches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from signalyze.config import Settings, get_settings
from signalyze.market.provider import MarketDataProvider, ProviderError
from signalyze.storage import Database
from signalyze.storage.repositories import upsert_market_bars
from signalyze.utils.logging import get_logger
from signalyze.utils.time import format_utc, parse_utc

logger = get_logger("signalyze.market.runner")


@dataclass
class FetchStats:
    instrument: str
    interval: str
    requested_segments: int = 0
    fetched_bars: int = 0
    cached_bars: int = 0
    errors: list[str] = field(default_factory=list)


def fetch_required_bars(
    *,
    db: Database,
    provider: MarketDataProvider,
    instrument: str = "XAUUSD",
    interval: str = "1min",
    max_holding_hours: float | None = None,
    settings: Settings | None = None,
) -> FetchStats:
    settings = settings or get_settings()
    max_holding_hours = max_holding_hours or settings.evaluate.max_holding_hours

    rows = db.conn.execute(
        "SELECT MIN(timestamp_utc) AS min_ts, MAX(timestamp_utc) AS max_ts FROM signals "
        "WHERE instrument = ?",
        (instrument,),
    ).fetchone()
    if rows is None or rows["min_ts"] is None:
        logger.info("No signals to align market data with; skipping fetch.")
        return FetchStats(instrument=instrument, interval=interval)

    range_start = parse_utc(rows["min_ts"])
    range_end = parse_utc(rows["max_ts"]) + timedelta(hours=max_holding_hours)

    cached_rows = db.conn.execute(
        "SELECT COUNT(*) AS n FROM market_bars WHERE instrument = ? AND interval = ? "
        "AND timestamp_utc BETWEEN ? AND ?",
        (instrument, interval, format_utc(range_start), format_utc(range_end)),
    ).fetchone()
    stats = FetchStats(
        instrument=instrument,
        interval=interval,
        cached_bars=int(cached_rows["n"]) if cached_rows else 0,
    )

    gaps = _compute_gaps(
        db=db,
        instrument=instrument,
        interval=interval,
        range_start=range_start,
        range_end=range_end,
    )
    stats.requested_segments = len(gaps)
    if not gaps:
        logger.info("Market data fully cached for %s [%s -> %s]", instrument, range_start, range_end)
        return stats

    for gap_start, gap_end in gaps:
        logger.info(
            "Fetching gap for %s: %s -> %s",
            instrument,
            format_utc(gap_start),
            format_utc(gap_end),
        )
        try:
            bars = provider.fetch_bars(
                instrument=instrument,
                interval=interval,
                start_utc=format_utc(gap_start),
                end_utc=format_utc(gap_end),
            )
        except ProviderError as exc:
            msg = f"{format_utc(gap_start)}..{format_utc(gap_end)}: {exc}"
            logger.error("Provider error: %s", msg)
            stats.errors.append(msg)
            continue

        if not bars:
            continue
        with db.transaction() as conn:
            upsert_market_bars(conn, bars)
        stats.fetched_bars += len(bars)

    return stats


def _compute_gaps(
    *,
    db: Database,
    instrument: str,
    interval: str,
    range_start: datetime,
    range_end: datetime,
    coverage_ratio: float = 0.5,
) -> list[tuple[datetime, datetime]]:
    """Split the requested range into one chunk per day and flag any chunk whose
    cached bar count falls below `coverage_ratio` of its slice-specific maximum.

    The threshold is *slice-proportional*, not day-proportional, so partial slices
    at the head/tail of the range can still be considered covered.
    """
    minutes_per_bar = _interval_minutes(interval)
    one_day = timedelta(days=1)

    gaps: list[tuple[datetime, datetime]] = []
    cursor = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cursor < range_end:
        day_end = cursor + one_day
        slice_start = max(cursor, range_start)
        slice_end = min(day_end, range_end)
        slice_minutes = (slice_end - slice_start).total_seconds() / 60.0
        expected_bars = max(1, int(slice_minutes / minutes_per_bar))
        threshold = max(1, int(expected_bars * coverage_ratio))

        row = db.conn.execute(
            "SELECT COUNT(*) AS n FROM market_bars WHERE instrument = ? AND interval = ? "
            "AND timestamp_utc >= ? AND timestamp_utc < ?",
            (instrument, interval, format_utc(slice_start), format_utc(slice_end)),
        ).fetchone()
        n = int(row["n"]) if row else 0
        if n < threshold:
            gaps.append((slice_start, slice_end))
        cursor = day_end
    return gaps


def _interval_minutes(interval: str) -> int:
    mapping = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "1h": 60}
    if interval not in mapping:
        raise ValueError(f"Unsupported interval: {interval}")
    return mapping[interval]
