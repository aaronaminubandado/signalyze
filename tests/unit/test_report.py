"""Unit tests for the static HTML report."""

from __future__ import annotations

from pathlib import Path

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
from signalyze.report import render_html_report
from signalyze.storage import Database
from signalyze.storage.repositories import (
    upsert_actual_outcome,
    upsert_messages,
    upsert_reported_outcome,
    upsert_signal,
)


def _seed(db: Database, *, signal_id: str, reported: OutcomeState, actual: OutcomeState) -> None:
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
        upsert_actual_outcome(
            conn,
            ActualOutcome(
                signal_id=signal_id,
                final_state=actual,
                realized_pips=100.0 if actual == OutcomeState.WIN else -50.0,
                win_policy=WinPolicy.ANY_TP,
                max_holding_hours=168.0,
                default_sl_policy="NONE",
                computed_at="2026-01-17T11:00:00Z",
                computed_version="v0.1",
            ),
        )


def test_html_report_includes_group_and_categories(tmp_db: Database, tmp_path: Path) -> None:
    _seed(tmp_db, signal_id="g:1", reported=OutcomeState.WIN, actual=OutcomeState.WIN)
    _seed(tmp_db, signal_id="g:2", reported=OutcomeState.WIN, actual=OutcomeState.LOSS)

    output = tmp_path / "report.html"
    rendered = render_html_report(db=tmp_db, output_path=output, title="Test")

    body = rendered.read_text(encoding="utf-8")
    assert "<title>Test</title>" in body
    assert "g" in body  # group id appears
    assert "REPORTED_WIN_ACTUAL_LOSS" in body
    assert "50.0%" in body  # reported_win_rate or actual_win_rate
