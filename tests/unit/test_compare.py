"""Unit tests for discrepancy categorization and per-group metrics."""

from __future__ import annotations

from signalyze.analytics import compute_group_metrics
from signalyze.compare import DiscrepancyCategory, compute_discrepancies
from signalyze.domain import (
    ActualOutcome,
    Direction,
    Message,
    OutcomeState,
    QualityFlag,
    ReportedOutcome,
    Signal,
    WinPolicy,
)
from signalyze.storage import Database
from signalyze.storage.repositories import (
    upsert_actual_outcome,
    upsert_messages,
    upsert_reported_outcome,
    upsert_signal,
)


def _seed(
    db: Database,
    *,
    signal_id: str,
    reported: OutcomeState | None,
    actual: OutcomeState | None,
    actual_pips: float | None = None,
) -> None:
    msg = Message(
        message_uid=signal_id,
        group_id="g",
        message_id=int(signal_id.split(":")[-1]),
        timestamp_utc="2026-01-17T10:00:00Z",
        text="signal",
        ingested_at="2026-01-17T10:00:00Z",
        ingest_method="csv_backfill",
    )
    with db.transaction() as conn:
        upsert_messages(conn, [msg])

    signal = Signal(
        signal_id=signal_id,
        message_uid=signal_id,
        group_id="g",
        timestamp_utc="2026-01-17T10:00:00Z",
        direction=Direction.BUY,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0],
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at="2026-01-17T10:00:00Z",
    )
    with db.transaction() as conn:
        upsert_signal(conn, signal)

    if reported is not None:
        with db.transaction() as conn:
            upsert_reported_outcome(
                conn,
                ReportedOutcome(
                    signal_id=signal_id,
                    final_state=reported,
                    source_follow_up_count=1,
                    computed_at="2026-01-17T11:00:00Z",
                    computed_version="v0.1",
                ),
            )

    if actual is not None:
        with db.transaction() as conn:
            upsert_actual_outcome(
                conn,
                ActualOutcome(
                    signal_id=signal_id,
                    final_state=actual,
                    realized_pips=actual_pips,
                    win_policy=WinPolicy.ANY_TP,
                    max_holding_hours=168.0,
                    default_sl_policy="NONE",
                    computed_at="2026-01-17T11:00:00Z",
                    computed_version="v0.1",
                ),
            )


def test_categorizes_overstated_wins(tmp_db: Database) -> None:
    _seed(tmp_db, signal_id="g:1", reported=OutcomeState.WIN, actual=OutcomeState.LOSS, actual_pips=-50.0)
    _seed(tmp_db, signal_id="g:2", reported=OutcomeState.WIN, actual=OutcomeState.WIN, actual_pips=100.0)
    _seed(tmp_db, signal_id="g:3", reported=OutcomeState.WIN, actual=OutcomeState.OPEN_AT_EXPIRY)

    rows = compute_discrepancies(db=tmp_db)
    by_cat = {row.signal_id: row.category for row in rows}
    assert by_cat["g:1"] == DiscrepancyCategory.REPORTED_WIN_ACTUAL_LOSS
    assert by_cat["g:2"] == DiscrepancyCategory.AGREES
    assert by_cat["g:3"] == DiscrepancyCategory.REPORTED_WIN_ACTUAL_OPEN


def test_categorizes_censored_outcomes(tmp_db: Database) -> None:
    _seed(tmp_db, signal_id="g:1", reported=OutcomeState.NO_REPORT, actual=OutcomeState.LOSS)
    _seed(tmp_db, signal_id="g:2", reported=OutcomeState.OPEN, actual=OutcomeState.WIN)
    _seed(tmp_db, signal_id="g:3", reported=None, actual=None)

    rows = compute_discrepancies(db=tmp_db)
    by_cat = {row.signal_id: row.category for row in rows}
    assert by_cat["g:1"] == DiscrepancyCategory.REPORTED_NO_REPORT_ACTUAL_LOSS
    assert by_cat["g:2"] == DiscrepancyCategory.REPORTED_OPEN_ACTUAL_WIN
    assert by_cat["g:3"] == DiscrepancyCategory.INSUFFICIENT_DATA


def test_group_metrics_compute_gaps(tmp_db: Database) -> None:
    _seed(tmp_db, signal_id="g:1", reported=OutcomeState.WIN, actual=OutcomeState.WIN, actual_pips=100.0)
    _seed(tmp_db, signal_id="g:2", reported=OutcomeState.WIN, actual=OutcomeState.LOSS, actual_pips=-50.0)
    _seed(tmp_db, signal_id="g:3", reported=OutcomeState.WIN, actual=OutcomeState.WIN, actual_pips=70.0)
    _seed(tmp_db, signal_id="g:4", reported=OutcomeState.LOSS, actual=OutcomeState.LOSS, actual_pips=-30.0)

    metrics = compute_group_metrics(db=tmp_db, group_id="g")
    assert metrics.n_signals == 4
    assert metrics.reported_win_rate == 0.75  # 3 wins / 4 decided
    assert metrics.actual_win_rate == 0.5  # 2 wins / 4 decided
    assert metrics.win_rate_gap == 0.25
    assert metrics.avg_realized_pips == 22.5  # (100 + -50 + 70 + -30) / 4
