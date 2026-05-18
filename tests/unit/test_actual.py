"""Unit tests for the walk-forward actual-performance simulator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from signalyze.domain import (
    Direction,
    MarketBar,
    Message,
    OutcomeState,
    QualityFlag,
    Signal,
    WinPolicy,
)
from signalyze.evaluate import SimulationConfig, simulate_all
from signalyze.storage import Database
from signalyze.storage.repositories import (
    fetch_actual_outcome,
    upsert_market_bars,
    upsert_messages,
    upsert_signal,
)

BASE = datetime(2026, 1, 17, 10, 0, tzinfo=UTC)


def _ts(offset_minutes: int) -> str:
    return (BASE + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_signal(
    db: Database,
    *,
    direction: Direction,
    entry: float | None,
    stop_loss: float | None,
    take_profits: list[float],
    timestamp: str = _ts(0),
) -> Signal:
    msg = Message(
        message_uid="g:sig",
        group_id="g",
        message_id=1,
        timestamp_utc=timestamp,
        text="signal",
        ingested_at=timestamp,
        ingest_method="csv_backfill",
    )
    with db.transaction() as conn:
        upsert_messages(conn, [msg])
    signal = Signal(
        signal_id=msg.message_uid,
        message_uid=msg.message_uid,
        group_id="g",
        timestamp_utc=timestamp,
        direction=direction,
        instrument="XAUUSD",
        entry=entry,
        stop_loss=stop_loss,
        take_profits=take_profits,
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at=timestamp,
    )
    with db.transaction() as conn:
        upsert_signal(conn, signal)
    return signal


def _make_bars(prices: list[tuple[float, float, float, float]]) -> list[MarketBar]:
    fetched = "2026-01-17T11:00:00Z"
    bars: list[MarketBar] = []
    for i, (o, h, low, c) in enumerate(prices):
        bars.append(
            MarketBar(
                instrument="XAUUSD",
                interval="1min",
                timestamp_utc=_ts(i),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=10.0,
                provider="csv",
                fetched_at=fetched,
            )
        )
    return bars


def _insert_bars(db: Database, bars: list[MarketBar]) -> None:
    with db.transaction() as conn:
        upsert_market_bars(conn, bars)


def test_long_first_touch_tp_yields_win(tmp_db: Database) -> None:
    _seed_signal(
        tmp_db,
        direction=Direction.BUY,
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0, 4720.0],
    )
    bars = _make_bars(
        [
            (4700.0, 4702.0, 4699.0, 4701.0),
            (4701.0, 4703.0, 4700.5, 4702.5),
            (4702.5, 4715.0, 4702.0, 4711.0),  # TP1 hit (high=4715 >= 4710)
        ]
    )
    _insert_bars(tmp_db, bars)

    simulate_all(db=tmp_db)
    outcome = fetch_actual_outcome(tmp_db.conn, "g:sig")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.WIN
    assert outcome.first_touch_event == "TP1"
    assert outcome.first_touch_price == 4710.0
    assert outcome.realized_pips == 100.0  # 10 USD * 10 pips/USD
    assert outcome.realized_rr == 1.0  # 10/10


def test_short_sl_yields_loss(tmp_db: Database) -> None:
    _seed_signal(
        tmp_db,
        direction=Direction.SELL,
        entry=4700.0,
        stop_loss=4710.0,
        take_profits=[4690.0],
    )
    bars = _make_bars(
        [
            (4700.0, 4702.0, 4699.0, 4701.0),
            (4701.0, 4712.0, 4701.0, 4711.0),  # high=4712 hits SL=4710
        ]
    )
    _insert_bars(tmp_db, bars)

    simulate_all(db=tmp_db)
    outcome = fetch_actual_outcome(tmp_db.conn, "g:sig")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.LOSS
    assert outcome.first_touch_event == "SL"
    assert outcome.realized_pips < 0


def test_same_bar_sl_and_tp_is_ambiguous(tmp_db: Database) -> None:
    _seed_signal(
        tmp_db,
        direction=Direction.BUY,
        entry=4700.0,
        stop_loss=4695.0,
        take_profits=[4705.0],
    )
    bars = _make_bars(
        [
            (4700.0, 4706.0, 4694.0, 4699.0),  # straddles SL=4695 and TP1=4705
        ]
    )
    _insert_bars(tmp_db, bars)

    simulate_all(db=tmp_db)
    outcome = fetch_actual_outcome(tmp_db.conn, "g:sig")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.AMBIGUOUS
    assert outcome.first_touch_event is not None
    assert "AMBIGUOUS" in outcome.first_touch_event


def test_expiry_with_no_touch(tmp_db: Database) -> None:
    _seed_signal(
        tmp_db,
        direction=Direction.BUY,
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0],
    )
    bars = _make_bars([(4700.0, 4702.0, 4699.0, 4701.0)] * 30)
    _insert_bars(tmp_db, bars)

    simulate_all(
        db=tmp_db,
        config=SimulationConfig(
            win_policy=WinPolicy.ANY_TP,
            max_holding_hours=1.0,
            default_sl_policy="NONE",
        ),
    )
    outcome = fetch_actual_outcome(tmp_db.conn, "g:sig")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.OPEN_AT_EXPIRY
    assert outcome.first_touch_event == "EXPIRY"


def test_no_bars_yields_insufficient_data(tmp_db: Database) -> None:
    _seed_signal(
        tmp_db,
        direction=Direction.BUY,
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0],
    )
    simulate_all(db=tmp_db)
    outcome = fetch_actual_outcome(tmp_db.conn, "g:sig")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.INSUFFICIENT_DATA
