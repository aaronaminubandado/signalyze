"""Structured follow-up event model."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FollowUpEventType(StrEnum):
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    BE_MOVED = "BE_MOVED"
    SL_MOVED = "SL_MOVED"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    UPDATE = "UPDATE"
    CANCEL = "CANCEL"
    AMBIGUOUS = "AMBIGUOUS"


class FollowUpEvent(BaseModel):
    """A parsed follow-up message claiming an outcome or update for a prior signal."""

    model_config = ConfigDict(frozen=True)

    follow_up_id: str
    message_uid: str
    group_id: str
    timestamp_utc: str

    event_type: FollowUpEventType
    tp_index: int | None = None
    claimed_price: float | None = None
    claimed_pips: float | None = None
    new_stop_loss: float | None = None

    parse_method: str
    parse_confidence: float
    parse_version: str
    parse_reasons: list[str] = Field(default_factory=list)
    parsed_at: str
