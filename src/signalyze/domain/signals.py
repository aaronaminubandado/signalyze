"""Structured trading-signal model. The single schema both rules and LLM output must satisfy."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class QualityFlag(StrEnum):
    COMPLETE = "COMPLETE"
    MISSING_SL = "MISSING_SL"
    MISSING_TP = "MISSING_TP"
    MISSING_BOTH = "MISSING_BOTH"
    ENTRY_RANGE = "ENTRY_RANGE"


class Signal(BaseModel):
    """A parsed trading signal. Either `entry` is set, or both `entry_low`/`entry_high` are set."""

    model_config = ConfigDict(frozen=True)

    signal_id: str
    message_uid: str
    group_id: str
    timestamp_utc: str
    direction: Direction
    instrument: str

    entry: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    take_profits: list[float] = Field(default_factory=list)

    quality_flag: QualityFlag
    parse_method: str  # "rules" | "llm"
    parse_confidence: float
    parse_version: str
    parse_reasons: list[str] = Field(default_factory=list)
    parsed_at: str

    @model_validator(mode="after")
    def _validate_entry(self) -> Signal:
        has_point = self.entry is not None
        has_range = self.entry_low is not None and self.entry_high is not None
        if not has_point and not has_range:
            raise ValueError("Signal requires either `entry` or both `entry_low` and `entry_high`.")
        if (
            has_range
            and self.entry_low is not None
            and self.entry_high is not None
            and self.entry_low > self.entry_high
        ):
            raise ValueError("`entry_low` must be <= `entry_high`.")
        return self

    @property
    def effective_entry(self) -> float:
        """Single representative entry price, used by simulators."""
        if self.entry is not None:
            return self.entry
        assert self.entry_low is not None and self.entry_high is not None
        return (self.entry_low + self.entry_high) / 2.0
