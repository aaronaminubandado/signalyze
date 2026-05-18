"""Raw Telegram message and its per-message classification."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MessageClass(StrEnum):
    """Classification bucket for a single message."""

    SIGNAL = "SIGNAL"
    FOLLOW_UP = "FOLLOW_UP"
    NOISE = "NOISE"
    UNCERTAIN = "UNCERTAIN"


class Message(BaseModel):
    """One raw Telegram message. Immutable after ingestion."""

    model_config = ConfigDict(frozen=True)

    message_uid: str
    group_id: str
    message_id: int
    timestamp_utc: str
    sender_id: str | None = None
    text: str = ""
    reply_to_msg_id: int | None = None
    views: int | None = None
    forwards: int | None = None
    reply_count: int | None = None
    ingested_at: str
    ingest_method: str  # "telethon" | "csv_backfill"

    @staticmethod
    def make_uid(group_id: str, message_id: int | str) -> str:
        """Stable identifier across the system."""
        return f"{group_id}:{message_id}"


class MessageClassification(BaseModel):
    """Per-message bucket assignment with confidence."""

    model_config = ConfigDict(frozen=True)

    message_uid: str
    message_class: MessageClass = Field(alias="class")
    confidence: float
    method: str  # "rules" | "llm"
    reasons: list[str] = Field(default_factory=list)
    classifier_version: str
    classified_at: str
