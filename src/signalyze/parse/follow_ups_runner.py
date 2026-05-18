"""Orchestrate follow-up extraction across FOLLOW_UP-classified messages."""

from __future__ import annotations

from dataclasses import dataclass

from signalyze.config import Settings, get_settings
from signalyze.domain import FollowUpEvent, Message, MessageClass
from signalyze.llm import LLMClient
from signalyze.parse.follow_ups_llm import llm_parse_follow_up
from signalyze.parse.follow_ups_rules import (
    FollowUpRuleParser,
    ParsedFollowUpPayload,
)
from signalyze.storage import Database
from signalyze.storage.repositories import (
    fetch_messages_by_class,
    upsert_follow_up,
)
from signalyze.utils.logging import get_logger
from signalyze.utils.time import now_utc_iso

logger = get_logger("signalyze.parse.follow_ups_runner")


@dataclass
class FollowUpExtractStats:
    candidates: int = 0
    parsed: int = 0
    rules_parsed: int = 0
    llm_parsed: int = 0
    rejected: int = 0


def extract_follow_ups(
    *,
    db: Database,
    rule_parser: FollowUpRuleParser | None = None,
    llm_client: LLMClient | None = None,
    use_llm: bool = True,
    group_id: str | None = None,
    settings: Settings | None = None,
) -> FollowUpExtractStats:
    settings = settings or get_settings()
    rule_parser = rule_parser or FollowUpRuleParser(settings)
    threshold = settings.parse.llm_escalation_threshold
    stats = FollowUpExtractStats()
    candidates = fetch_messages_by_class(
        db.conn, MessageClass.FOLLOW_UP, group_id=group_id
    )
    stats.candidates = len(candidates)

    for message in candidates:
        payload, confidence, method, reasons = _parse_one(
            text=message.text,
            rule_parser=rule_parser,
            llm_client=llm_client if use_llm else None,
            threshold=threshold,
        )
        if payload is None:
            stats.rejected += 1
            continue

        event = _to_follow_up(
            message=message,
            payload=payload,
            confidence=confidence,
            parse_method=method,
            reasons=reasons,
            parse_version=rule_parser.version,
        )
        with db.transaction() as conn:
            upsert_follow_up(conn, event)

        stats.parsed += 1
        if method == "rules":
            stats.rules_parsed += 1
        else:
            stats.llm_parsed += 1

    return stats


def _parse_one(
    *,
    text: str,
    rule_parser: FollowUpRuleParser,
    llm_client: LLMClient | None,
    threshold: float,
) -> tuple[ParsedFollowUpPayload | None, float, str, list[str]]:
    result = rule_parser.parse_text(text)
    if result.payload is not None and result.confidence >= threshold:
        return result.payload, result.confidence, "rules", result.reasons

    if llm_client is not None and llm_client.is_available:
        payload, llm_confidence, llm_reasons = llm_parse_follow_up(text, llm_client)
        if payload is not None:
            return payload, llm_confidence, "llm", [*result.reasons, *llm_reasons]
        return None, llm_confidence, "llm", [*result.reasons, *llm_reasons]

    return result.payload, result.confidence, "rules", result.reasons


def _to_follow_up(
    *,
    message: Message,
    payload: ParsedFollowUpPayload,
    confidence: float,
    parse_method: str,
    reasons: list[str],
    parse_version: str,
) -> FollowUpEvent:
    return FollowUpEvent(
        follow_up_id=message.message_uid,
        message_uid=message.message_uid,
        group_id=message.group_id,
        timestamp_utc=message.timestamp_utc,
        event_type=payload.event_type,
        tp_index=payload.tp_index,
        claimed_price=payload.claimed_price,
        claimed_pips=payload.claimed_pips,
        new_stop_loss=payload.new_stop_loss,
        parse_method=parse_method,
        parse_confidence=confidence,
        parse_version=parse_version,
        parse_reasons=reasons,
        parsed_at=now_utc_iso(),
    )
