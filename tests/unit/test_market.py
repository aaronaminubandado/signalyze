"""Unit tests for the market data abstraction + CSV provider + runner gap detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from signalyze.domain import Direction, Message, QualityFlag, Signal
from signalyze.market import fetch_required_bars
from signalyze.market.providers import CSVProvider
from signalyze.storage import Database
from signalyze.storage.repositories import (
    fetch_market_bars,
    upsert_messages,
    upsert_signal,
)


def _make_csv(path: Path, *, base: datetime, n: int) -> None:
    lines = ["timestamp_utc,open,high,low,close,volume"]
    for i in range(n):
        ts = (base + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        price = 4700.0 + (i % 10) * 0.5
        lines.append(f"{ts},{price},{price + 1.0},{price - 1.0},{price + 0.5},10")
    path.write_text("\n".join(lines), encoding="utf-8")


def _seed_signal(db: Database, *, timestamp: str) -> None:
    msg = Message(
        message_uid=f"g:{timestamp}",
        group_id="g",
        message_id=int(timestamp[-4:].replace(":", "").replace("Z", "") or "1"),
        timestamp_utc=timestamp,
        text="signal",
        ingested_at=timestamp,
        ingest_method="csv_backfill",
    )
    with db.transaction() as conn:
        upsert_messages(conn, [msg])

    signal = Signal(
        signal_id=msg.message_uid,
        message_uid=msg.message_uid,
        group_id="g",
        timestamp_utc=timestamp,
        direction=Direction.BUY,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0],
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at=timestamp,
    )
    with db.transaction() as conn:
        upsert_signal(conn, signal)


def test_csv_provider_filters_to_requested_range(tmp_path: Path) -> None:
    csv_path = tmp_path / "bars.csv"
    base = datetime(2026, 1, 17, 10, 0, tzinfo=UTC)
    _make_csv(csv_path, base=base, n=60)
    provider = CSVProvider(csv_path)

    bars = provider.fetch_bars(
        instrument="XAUUSD",
        interval="1min",
        start_utc="2026-01-17T10:10:00Z",
        end_utc="2026-01-17T10:20:00Z",
    )
    assert len(bars) == 10
    assert bars[0].timestamp_utc == "2026-01-17T10:10:00Z"
    assert all(bar.provider == "csv" for bar in bars)


def test_runner_fetches_and_skips_when_cached(tmp_db: Database, tmp_path: Path) -> None:
    csv_path = tmp_path / "bars.csv"
    base = datetime(2026, 1, 17, 10, 0, tzinfo=UTC)
    _make_csv(csv_path, base=base, n=24 * 60 * 7)
    provider = CSVProvider(csv_path)

    _seed_signal(tmp_db, timestamp="2026-01-17T10:00:00Z")
    stats = fetch_required_bars(
        db=tmp_db,
        provider=provider,
        instrument="XAUUSD",
        interval="1min",
    )
    assert stats.fetched_bars > 0

    cached_count = tmp_db.conn.execute(
        "SELECT COUNT(*) AS n FROM market_bars"
    ).fetchone()["n"]
    assert cached_count == stats.fetched_bars

    second = fetch_required_bars(
        db=tmp_db,
        provider=provider,
        instrument="XAUUSD",
        interval="1min",
    )
    assert second.fetched_bars == 0
    assert second.cached_bars >= cached_count


def test_runner_no_signals_returns_empty(tmp_db: Database, tmp_path: Path) -> None:
    csv_path = tmp_path / "bars.csv"
    _make_csv(csv_path, base=datetime(2026, 1, 17, tzinfo=UTC), n=1)
    provider = CSVProvider(csv_path)
    stats = fetch_required_bars(
        db=tmp_db,
        provider=provider,
        instrument="XAUUSD",
        interval="1min",
    )
    assert stats.fetched_bars == 0
    assert fetch_market_bars(
        tmp_db.conn,
        instrument="XAUUSD",
        interval="1min",
        start_utc="2026-01-17T00:00:00Z",
        end_utc="2026-01-18T00:00:00Z",
    ) == []
