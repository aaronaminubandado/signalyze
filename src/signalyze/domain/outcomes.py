"""Reported and actual trade outcomes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class OutcomeState(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAK_EVEN = "BREAK_EVEN"
    OPEN = "OPEN"
    OPEN_AT_EXPIRY = "OPEN_AT_EXPIRY"
    AMBIGUOUS = "AMBIGUOUS"
    NO_REPORT = "NO_REPORT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class WinPolicy(StrEnum):
    """Definitions of 'win' for actual-performance simulation."""

    ANY_TP = "ANY_TP"
    FIRST_TP_ONLY = "FIRST_TP_ONLY"
    FINAL_CLOSE_VS_ENTRY = "FINAL_CLOSE_VS_ENTRY"


class ReportedOutcome(BaseModel):
    """Outcome aggregated purely from a signal's linked follow-up messages."""

    model_config = ConfigDict(frozen=True)

    signal_id: str
    final_state: OutcomeState
    max_tp_hit: int | None = None
    claimed_pips: float | None = None
    closed_at_utc: str | None = None
    source_follow_up_count: int
    computed_at: str
    computed_version: str


class ActualOutcome(BaseModel):
    """Outcome derived from market data via walk-forward simulation."""

    model_config = ConfigDict(frozen=True)

    signal_id: str
    final_state: OutcomeState
    first_touch_event: str | None = None  # "TP1" | "TP2" | ... | "SL" | "EXPIRY"
    first_touch_price: float | None = None
    first_touch_at_utc: str | None = None
    realized_rr: float | None = None
    realized_pips: float | None = None
    bars_to_outcome: int | None = None
    win_policy: WinPolicy
    max_holding_hours: float
    default_sl_policy: str
    computed_at: str
    computed_version: str
