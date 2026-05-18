"""Unit tests for the rule-based message classifier."""

from __future__ import annotations

from signalyze.classify import RuleClassifier
from signalyze.domain import Message, MessageClass


def _msg(text: str, reply_to: int | None = None) -> Message:
    return Message(
        message_uid="g:1",
        group_id="g",
        message_id=1,
        timestamp_utc="2026-01-17T10:00:00Z",
        text=text,
        reply_to_msg_id=reply_to,
        ingested_at="2026-01-17T10:00:00Z",
        ingest_method="csv_backfill",
    )


def test_full_xauusd_signal_classified_as_signal() -> None:
    c = RuleClassifier()
    text = "#XAUUSD SELL 4664\nSL 4678\nTP 4661\nTP 4658\nTP 4655"
    result = c.classify(_msg(text))
    assert result.message_class == MessageClass.SIGNAL
    assert result.confidence >= 0.7


def test_tp_hit_is_follow_up() -> None:
    c = RuleClassifier()
    result = c.classify(_msg("TP1 hit +30 pips profit done"))
    assert result.message_class == MessageClass.FOLLOW_UP


def test_promo_is_noise() -> None:
    c = RuleClassifier()
    text = "VIP_SIGNALS account management Service Available. Contact me. @vipseller"
    assert c.classify(_msg(text)).message_class == MessageClass.NOISE


def test_empty_text_is_noise() -> None:
    c = RuleClassifier()
    assert c.classify(_msg("")).message_class == MessageClass.NOISE


def test_implausible_price_does_not_count_as_signal_price() -> None:
    c = RuleClassifier()
    # A bare "SELL 2" or "SELL 80" without TP/SL price columns must not become a signal.
    result = c.classify(_msg("SELL 2"))
    assert result.message_class in {MessageClass.UNCERTAIN, MessageClass.NOISE}


def test_xaut_token_promo_is_noise() -> None:
    c = RuleClassifier()
    text = "NEW USER DEAL - Buy XAUT at 50% OFF\nJoin Here: https://example.com"
    assert c.classify(_msg(text)).message_class == MessageClass.NOISE
