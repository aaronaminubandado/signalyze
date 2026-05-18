"""Market OHLCV bar model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MarketBar(BaseModel):
    """One OHLCV bar for an instrument at a fixed interval."""

    model_config = ConfigDict(frozen=True)

    instrument: str
    interval: str  # "1min" | "5min" | "1hour" | ...
    timestamp_utc: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    provider: str
    fetched_at: str

    @property
    def bar_id(self) -> str:
        return f"{self.instrument}:{self.interval}:{self.timestamp_utc}:{self.provider}"
