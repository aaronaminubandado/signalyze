"""Signal-to-follow-up link model."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LinkMethod(StrEnum):
    REPLY_TO = "reply_to"
    TEMPORAL_NUMERIC = "temporal_numeric"
    RECENT_OPEN = "recent_open"
    LLM = "llm"


class SignalFollowUpLink(BaseModel):
    """One follow-up may link to one signal. Confidence and method are always recorded."""

    model_config = ConfigDict(frozen=True)

    link_id: str
    follow_up_id: str
    signal_id: str
    link_method: LinkMethod
    link_confidence: float
    reasons: list[str] = Field(default_factory=list)
    linked_at: str
    linker_version: str
