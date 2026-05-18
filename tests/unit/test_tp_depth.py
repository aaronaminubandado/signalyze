"""Unit tests for the per-group TP-depth analytics."""

from __future__ import annotations

from signalyze.analytics import compute_tp_depth, iter_tp_depth
from signalyze.domain import (
    Direction,
    Message,
    OutcomeState,
    QualityFlag,
    ReportedOutcome,
    Signal,
)
from signalyze.storage import Database
from signalyze.storage.repositories import (
    upsert_messages,
    upsert_reported_outcome,
    upsert_signal,
)


def _seed_signal(
    db: Database,
    *,
    group_id: str,
    message_id: int,
    take_profits: list[float],
    reported_state: OutcomeState | None,
    max_tp_hit: int | None = None,
) -> None:
    uid = f"{group_id}:{message_id}"
    timestamp = f"2026-01-{17 + (message_id % 5):02d}T10:00:00Z"
    msg = Message(
        message_uid=uid,
        group_id=group_id,
        message_id=message_id,
        timestamp_utc=timestamp,
        text="signal",
        ingested_at=timestamp,
        ingest_method="csv_backfill",
    )
    signal = Signal(
        signal_id=uid,
        message_uid=uid,
        group_id=group_id,
        timestamp_utc=timestamp,
        direction=Direction.BUY,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=take_profits,
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at=timestamp,
    )
    with db.transaction() as conn:
        upsert_messages(conn, [msg])
        upsert_signal(conn, signal)
        if reported_state is not None:
            upsert_reported_outcome(
                conn,
                ReportedOutcome(
                    signal_id=uid,
                    final_state=reported_state,
                    max_tp_hit=max_tp_hit,
                    source_follow_up_count=1,
                    computed_at=timestamp,
                    computed_version="v0.1",
                ),
            )


def test_mixed_max_tp_hit_excludes_no_report_from_denom(tmp_db: Database) -> None:
    tps = [4710.0, 4720.0, 4730.0]
    _seed_signal(tmp_db, group_id="g", message_id=1, take_profits=tps,
                 reported_state=OutcomeState.WIN, max_tp_hit=1)
    _seed_signal(tmp_db, group_id="g", message_id=2, take_profits=tps,
                 reported_state=OutcomeState.WIN, max_tp_hit=3)
    _seed_signal(tmp_db, group_id="g", message_id=3, take_profits=tps,
                 reported_state=OutcomeState.NO_REPORT)

    depth = compute_tp_depth(db=tmp_db, group_id="g")
    assert depth.n_signals == 3
    assert depth.n_reported == 2
    assert depth.no_report_rate is not None
    assert abs(depth.no_report_rate - 1 / 3) < 1e-3
    assert depth.sl_hit_rate == 0.0
    assert depth.max_tp_level == 3

    assert depth.level(1) is not None
    assert depth.level(1).denom == 2
    assert depth.level(1).hits == 2
    assert depth.level(1).hit_rate == 1.0

    assert depth.level(2).denom == 2
    assert depth.level(2).hits == 1
    assert depth.level(2).hit_rate == 0.5

    assert depth.level(3).denom == 2
    assert depth.level(3).hits == 1
    assert depth.level(3).hit_rate == 0.5


def test_varying_tp_counts_shrinks_higher_denominators(tmp_db: Database) -> None:
    # Two signals with 2 TPs, two with 4 TPs.
    _seed_signal(tmp_db, group_id="g", message_id=11, take_profits=[1, 2],
                 reported_state=OutcomeState.WIN, max_tp_hit=2)
    _seed_signal(tmp_db, group_id="g", message_id=12, take_profits=[1, 2],
                 reported_state=OutcomeState.WIN, max_tp_hit=1)
    _seed_signal(tmp_db, group_id="g", message_id=13, take_profits=[1, 2, 3, 4],
                 reported_state=OutcomeState.WIN, max_tp_hit=4)
    _seed_signal(tmp_db, group_id="g", message_id=14, take_profits=[1, 2, 3, 4],
                 reported_state=OutcomeState.WIN, max_tp_hit=2)

    depth = compute_tp_depth(db=tmp_db, group_id="g")
    assert depth.max_tp_level == 4
    # TP1 denom = 4 (everyone defined TP1), all 4 reached TP1.
    assert depth.level(1).denom == 4
    assert depth.level(1).hits == 4
    # TP2 denom = 4, hits = 3 (msg 12 only reached TP1).
    assert depth.level(2).denom == 4
    assert depth.level(2).hits == 3
    # TP3 denom = 2 (only the 4-TP signals), hits = 1 (msg 13).
    assert depth.level(3).denom == 2
    assert depth.level(3).hits == 1
    assert depth.level(3).hit_rate == 0.5
    # TP4 denom = 2, hits = 1 (msg 13).
    assert depth.level(4).denom == 2
    assert depth.level(4).hits == 1


def test_group_with_only_no_report_yields_null_rates(tmp_db: Database) -> None:
    _seed_signal(tmp_db, group_id="quiet", message_id=21,
                 take_profits=[1.0, 2.0], reported_state=OutcomeState.NO_REPORT)
    _seed_signal(tmp_db, group_id="quiet", message_id=22,
                 take_profits=[1.0, 2.0], reported_state=None)

    depth = compute_tp_depth(db=tmp_db, group_id="quiet")
    assert depth.n_signals == 2
    assert depth.n_reported == 0
    assert depth.no_report_rate == 1.0
    assert depth.sl_hit_rate is None
    assert depth.max_tp_level == 2
    for stat in depth.tp_levels:
        assert stat.denom == 0
        assert stat.hits == 0
        assert stat.hit_rate is None


def test_loss_signals_count_toward_sl_rate_but_not_tp_hits(tmp_db: Database) -> None:
    _seed_signal(tmp_db, group_id="bears", message_id=31,
                 take_profits=[1.0, 2.0], reported_state=OutcomeState.LOSS)
    _seed_signal(tmp_db, group_id="bears", message_id=32,
                 take_profits=[1.0, 2.0], reported_state=OutcomeState.LOSS)
    _seed_signal(tmp_db, group_id="bears", message_id=33,
                 take_profits=[1.0, 2.0], reported_state=OutcomeState.WIN,
                 max_tp_hit=1)

    depth = compute_tp_depth(db=tmp_db, group_id="bears")
    assert depth.n_reported == 3
    assert depth.sl_hit_rate is not None
    assert abs(depth.sl_hit_rate - 2 / 3) < 1e-3
    assert depth.level(1).denom == 3
    assert depth.level(1).hits == 1
    assert depth.level(2).hits == 0


def test_iter_tp_depth_yields_every_group(tmp_db: Database) -> None:
    _seed_signal(tmp_db, group_id="alpha", message_id=41,
                 take_profits=[1.0, 2.0], reported_state=OutcomeState.WIN,
                 max_tp_hit=2)
    _seed_signal(tmp_db, group_id="beta", message_id=42,
                 take_profits=[1.0], reported_state=OutcomeState.WIN,
                 max_tp_hit=1)

    seen = {row.group_id: row for row in iter_tp_depth(db=tmp_db)}
    assert set(seen) == {"alpha", "beta"}
    assert seen["alpha"].max_tp_level == 2
    assert seen["beta"].max_tp_level == 1
