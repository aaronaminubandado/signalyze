"""Abstract market data provider protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from signalyze.domain import MarketBar


class ProviderError(RuntimeError):
    """Raised when a provider cannot satisfy a request (auth, rate-limit, etc.)."""


@runtime_checkable
class MarketDataProvider(Protocol):
    """Pure-fetch interface: a provider returns bars; persistence is the runner's job."""

    name: str

    def fetch_bars(
        self,
        *,
        instrument: str,
        interval: str,
        start_utc: str,
        end_utc: str,
    ) -> list[MarketBar]:
        """Return bars in [start_utc, end_utc) ordered ascending by timestamp.

        Implementations should:
          - tag each bar with `provider = self.name` and an ISO `fetched_at`.
          - chunk and rate-limit internally.
          - raise `ProviderError` on hard failures so callers can retry/skip.
        """
        ...
