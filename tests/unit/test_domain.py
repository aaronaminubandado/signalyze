"""Domain-model validation tests."""

from __future__ import annotations

import pytest

from signalyze.domain import (
    Direction,
    QualityFlag,
    Signal,
)


def _make_signal(**overrides: object) -> Signal:
    base: dict[str, object] = dict(
        signal_id="sig_test",
        message_uid="g:1",
        group_id="g",
        timestamp_utc="2026-01-17T10:00:00Z",
        direction=Direction.BUY,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0, 4720.0],
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=1.0,
        parse_version="v0.1",
        parsed_at="2026-01-17T10:00:01Z",
    )
    base.update(overrides)
    return Signal(**base)  # type: ignore[arg-type]


def test_signal_with_point_entry_is_valid() -> None:
    s = _make_signal()
    assert s.effective_entry == 4700.0


def test_signal_with_range_entry_is_valid() -> None:
    s = _make_signal(entry=None, entry_low=4700.0, entry_high=4710.0)
    assert s.effective_entry == 4705.0


def test_signal_without_any_entry_raises() -> None:
    with pytest.raises(ValueError):
        _make_signal(entry=None)


def test_signal_with_inverted_range_raises() -> None:
    with pytest.raises(ValueError):
        _make_signal(entry=None, entry_low=4710.0, entry_high=4700.0)
