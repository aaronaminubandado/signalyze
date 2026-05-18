"""Orchestrate signal extraction across classified messages."""

from __future__ import annotations

from dataclasses import dataclass

from signalyze.config import Settings, get_settings
from signalyze.domain import Message, MessageClass, Signal
from signalyze.llm import LLMClient
from signalyze.parse.signals_llm import llm_parse_signal
from signalyze.parse.signals_rules import ParsedSignalPayload, SignalRuleParser
from signalyze.storage import Database
from signalyze.storage.repositories import (
    fetch_messages_by_class,
    upsert_signal,
)
from signalyze.utils.logging import get_logger
from signalyze.utils.time import now_utc_iso

logger = get_logger("signalyze.parse.signals_runner")


@dataclass
class SignalExtractStats:
    """Summary of one signal-extraction run."""

    candidates: int = 0
    parsed: int = 0
    rules_parsed: int = 0
    llm_parsed: int = 0
    rejected: int = 0


def extract_signals(
    *,
    db: Database,
    rule_parser: SignalRuleParser | None = None,
    llm_client: LLMClient | None = None,
    use_llm: bool = True,
    group_id: str | None = None,
    settings: Settings | None = None,
) -> SignalExtractStats:
    """Parse every SIGNAL-classified message and upsert a `Signal` row."""
    settings = settings or get_settings()
    rule_parser = rule_parser or SignalRuleParser(settings)
    threshold = settings.parse.llm_escalation_threshold
    stats = SignalExtractStats()
    candidates = fetch_messages_by_class(
        db.conn, MessageClass.SIGNAL, group_id=group_id
    )
    stats.candidates = len(candidates)

    for message in candidates:
        payload, confidence, parse_method, reasons = _parse_one(
            text=message.text,
            rule_parser=rule_parser,
            llm_client=llm_client if use_llm else None,
            threshold=threshold,
        )
        if payload is None:
            stats.rejected += 1
            continue

        signal = _to_signal(
            message=message,
            payload=payload,
            confidence=confidence,
            parse_method=parse_method,
            reasons=reasons,
            parse_version=rule_parser.version,
        )
        with db.transaction() as conn:
            upsert_signal(conn, signal)

        stats.parsed += 1
        if parse_method == "rules":
            stats.rules_parsed += 1
        else:
            stats.llm_parsed += 1

    return stats


def _parse_one(
    *,
    text: str,
    rule_parser: SignalRuleParser,
    llm_client: LLMClient | None,
    threshold: float,
) -> tuple[ParsedSignalPayload | None, float, str, list[str]]:
    result = rule_parser.parse_text(text)
    if result.payload is not None and result.confidence >= threshold:
        return result.payload, result.confidence, "rules", result.reasons

    if llm_client is not None and llm_client.is_available:
        payload, llm_confidence, llm_reasons = llm_parse_signal(text, llm_client)
        if payload is not None:
            return payload, llm_confidence, "llm", result.reasons + llm_reasons
        return None, llm_confidence, "llm", result.reasons + llm_reasons

    return result.payload, result.confidence, "rules", result.reasons


def _to_signal(
    *,
    message: Message,
    payload: ParsedSignalPayload,
    confidence: float,
    parse_method: str,
    reasons: list[str],
    parse_version: str,
) -> Signal:
    return Signal(
        signal_id=message.message_uid,
        message_uid=message.message_uid,
        group_id=message.group_id,
        timestamp_utc=message.timestamp_utc,
        direction=payload.direction,
        instrument=payload.instrument,
        entry=payload.entry,
        entry_low=payload.entry_low,
        entry_high=payload.entry_high,
        stop_loss=payload.stop_loss,
        take_profits=payload.take_profits,
        quality_flag=payload.quality_flag,
        parse_method=parse_method,
        parse_confidence=confidence,
        parse_version=parse_version,
        parse_reasons=reasons,
        parsed_at=now_utc_iso(),
    )
