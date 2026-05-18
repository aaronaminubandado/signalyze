"""Unit tests for the deterministic signal parser."""

from __future__ import annotations

from signalyze.domain import Direction, QualityFlag
from signalyze.parse.signals_rules import SignalRuleParser


def test_parses_canonical_signal() -> None:
    p = SignalRuleParser()
    text = "#XAUUSD SELL 4664\nSL 4678\nTP 4661\nTP 4658\nTP 4655"
    r = p.parse_text(text)
    assert r.payload is not None
    assert r.payload.direction == Direction.SELL
    assert r.payload.entry == 4664.0
    assert r.payload.stop_loss == 4678.0
    assert r.payload.take_profits == [4661.0, 4658.0, 4655.0]
    assert r.payload.quality_flag == QualityFlag.COMPLETE
    assert r.confidence >= 0.9


def test_parses_entry_range() -> None:
    p = SignalRuleParser()
    text = "XAUUSD Sell 4675/4678\nTP1. @ 4672\nSL. @ 4686"
    r = p.parse_text(text)
    assert r.payload is not None
    assert r.payload.entry_low == 4675.0
    assert r.payload.entry_high == 4678.0
    assert r.payload.entry is None
    assert r.payload.stop_loss == 4686.0


def test_rejects_implausible_xauusd_entry() -> None:
    p = SignalRuleParser()
    r = p.parse_text("SELL 2")
    assert r.payload is None


def test_rejects_follow_up_text() -> None:
    p = SignalRuleParser()
    r = p.parse_text("TP1 hit +30 pips")
    assert r.payload is None


def test_missing_sl_yields_quality_flag() -> None:
    p = SignalRuleParser()
    r = p.parse_text("#XAUUSD BUY 4710\nTP 4720\nTP 4730")
    assert r.payload is not None
    assert r.payload.stop_loss is None
    assert r.payload.quality_flag == QualityFlag.MISSING_SL


def test_normalises_gold_to_xauusd() -> None:
    p = SignalRuleParser()
    r = p.parse_text("GOLD BUY NOW 4710\nSL 4700\nTP 4720")
    assert r.payload is not None
    assert r.payload.instrument == "XAUUSD"
