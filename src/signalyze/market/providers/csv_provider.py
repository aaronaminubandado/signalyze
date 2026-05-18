"""Offline CSV market data provider.

Reads bars from a CSV file on disk. Useful when an API key is unavailable, when
running CI without network access, or when a curated historical dump exists
locally. Expected columns: timestamp_utc, open, high, low, close [, volume].
"""

from __future__ import annotations

import csv
from pathlib import Path

from signalyze.domain import MarketBar
from signalyze.market.provider import MarketDataProvider, ProviderError
from signalyze.utils.time import now_utc_iso, parse_utc


class CSVProvider(MarketDataProvider):
    """Local CSV-backed provider, intended for tests and offline use."""

    name = "csv"

    def __init__(self, csv_path: Path) -> None:
        self._csv_path = Path(csv_path)

    def fetch_bars(
        self,
        *,
        instrument: str,
        interval: str,
        start_utc: str,
        end_utc: str,
    ) -> list[MarketBar]:
        if not self._csv_path.exists():
            raise ProviderError(f"CSV file not found: {self._csv_path}")
        start_dt = parse_utc(start_utc)
        end_dt = parse_utc(end_utc)
        fetched = now_utc_iso()

        bars: list[MarketBar] = []
        with self._csv_path.open(encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ts = (row.get("timestamp_utc") or "").strip()
                if not ts:
                    continue
                if not ts.endswith("Z"):
                    ts = ts + "Z"
                row_dt = parse_utc(ts)
                if row_dt < start_dt or row_dt >= end_dt:
                    continue
                try:
                    bars.append(
                        MarketBar(
                            instrument=instrument,
                            interval=interval,
                            timestamp_utc=ts,
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]) if row.get("volume") else None,
                            provider="csv",
                            fetched_at=fetched,
                        )
                    )
                except (KeyError, ValueError) as exc:
                    raise ProviderError(f"csv: malformed row: {row!r}") from exc
        bars.sort(key=lambda b: b.timestamp_utc)
        return bars
