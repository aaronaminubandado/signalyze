"""Twelve Data provider: 1-minute XAUUSD bars via REST."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from signalyze.domain import MarketBar
from signalyze.market.provider import MarketDataProvider, ProviderError
from signalyze.utils.logging import get_logger
from signalyze.utils.time import now_utc_iso, parse_utc

logger = get_logger("signalyze.market.twelvedata")

_BASE_URL = "https://api.twelvedata.com/time_series"
_MAX_BARS_PER_CALL = 5000
_SYMBOL_MAP = {"XAUUSD": "XAU/USD"}


class TwelveDataProvider(MarketDataProvider):
    """Twelve Data REST adapter.

    Requires `TWELVEDATA_API_KEY` in the environment. Twelve Data caps a single
    `time_series` request at 5000 bars, so we chunk the requested range.
    """

    name = "twelvedata"

    def __init__(self, api_key: str, *, http_timeout: float = 30.0) -> None:
        if not api_key:
            raise ProviderError("TWELVEDATA_API_KEY is not set")
        self._api_key = api_key
        self._timeout = http_timeout

    def fetch_bars(
        self,
        *,
        instrument: str,
        interval: str,
        start_utc: str,
        end_utc: str,
    ) -> list[MarketBar]:
        symbol = _SYMBOL_MAP.get(instrument, instrument)
        start = parse_utc(start_utc)
        end = parse_utc(end_utc)

        bars: list[MarketBar] = []
        chunk_minutes = _MAX_BARS_PER_CALL if interval == "1min" else _MAX_BARS_PER_CALL // 5
        chunk = timedelta(minutes=chunk_minutes)

        cursor = start
        with httpx.Client(timeout=self._timeout) as client:
            while cursor < end:
                slice_end = min(cursor + chunk, end)
                logger.info(
                    "twelvedata: fetching %s [%s -> %s]",
                    symbol,
                    cursor.isoformat(),
                    slice_end.isoformat(),
                )
                payload = self._fetch_one(
                    client=client,
                    symbol=symbol,
                    interval=interval,
                    start_dt=cursor.isoformat(),
                    end_dt=slice_end.isoformat(),
                )
                for raw in payload:
                    bars.append(_to_bar(instrument=instrument, interval=interval, raw=raw))
                cursor = slice_end
        bars.sort(key=lambda b: b.timestamp_utc)
        return bars

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _fetch_one(
        self,
        *,
        client: httpx.Client,
        symbol: str,
        interval: str,
        start_dt: str,
        end_dt: str,
    ) -> list[dict[str, Any]]:
        response = client.get(
            _BASE_URL,
            params={
                "symbol": symbol,
                "interval": interval,
                "start_date": start_dt,
                "end_date": end_dt,
                "format": "JSON",
                "timezone": "UTC",
                "apikey": self._api_key,
            },
        )
        if response.status_code != 200:
            raise ProviderError(
                f"twelvedata returned HTTP {response.status_code}: {response.text[:200]}"
            )
        body = response.json()
        status = body.get("status")
        if status == "error":
            raise ProviderError(f"twelvedata error: {body.get('message')}")
        values = body.get("values") or []
        return [v for v in values if isinstance(v, dict)]


def _to_bar(*, instrument: str, interval: str, raw: dict[str, Any]) -> MarketBar:
    timestamp = str(raw.get("datetime", "")).replace(" ", "T")
    if not timestamp.endswith("Z"):
        timestamp = timestamp + "Z"
    try:
        return MarketBar(
            instrument=instrument,
            interval=interval,
            timestamp_utc=timestamp,
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=float(raw["volume"]) if raw.get("volume") not in (None, "") else None,
            provider="twelvedata",
            fetched_at=now_utc_iso(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(f"twelvedata: malformed bar: {raw!r}") from exc
