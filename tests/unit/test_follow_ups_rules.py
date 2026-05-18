"""Unit tests for the deterministic follow-up parser."""

from __future__ import annotations

from signalyze.domain import FollowUpEventType
from signalyze.parse.follow_ups_rules import FollowUpRuleParser


def test_parses_tp_hit_with_index_and_pips() -> None:
    p = FollowUpRuleParser()
    r = p.parse_text("#XAUUSD TP1 HIT 30+ PIPS PROFIT DONE")
    assert r.payload is not None
    assert r.payload.event_type == FollowUpEventType.TP_HIT
    assert r.payload.tp_index == 1
    assert r.payload.claimed_pips == 30.0


def test_parses_sl_hit() -> None:
    p = FollowUpRuleParser()
    r = p.parse_text("UNFORTUNATELY SL HIT GUYS")
    assert r.payload is not None
    assert r.payload.event_type == FollowUpEventType.SL_HIT


def test_parses_be_moved() -> None:
    p = FollowUpRuleParser()
    r = p.parse_text("Move SL to entry / break even")
    assert r.payload is not None
    assert r.payload.event_type == FollowUpEventType.BE_MOVED


def test_parses_sl_moved_with_price() -> None:
    p = FollowUpRuleParser()
    r = p.parse_text("Move SL to 4710")
    assert r.payload is not None
    assert r.payload.event_type == FollowUpEventType.SL_MOVED
    assert r.payload.new_stop_loss == 4710.0


def test_cancel_takes_priority() -> None:
    p = FollowUpRuleParser()
    r = p.parse_text("Cancel the previous signal — TP1 hit didn't matter anyway")
    assert r.payload is not None
    assert r.payload.event_type == FollowUpEventType.CANCEL


def test_pips_only_is_update() -> None:
    p = FollowUpRuleParser()
    r = p.parse_text("+45 pips")
    assert r.payload is not None
    assert r.payload.event_type == FollowUpEventType.UPDATE
    assert r.payload.claimed_pips == 45.0


def test_returns_none_for_chatter() -> None:
    p = FollowUpRuleParser()
    r = p.parse_text("See you in our live trading session later!")
    assert r.payload is None
