"""Unit tests for reported-outcome derivation."""

from __future__ import annotations

from signalyze.domain import (
    Direction,
    FollowUpEvent,
    FollowUpEventType,
    LinkMethod,
    Message,
    OutcomeState,
    QualityFlag,
    Signal,
    SignalFollowUpLink,
)
from signalyze.evaluate import compute_reported_outcomes
from signalyze.storage import Database
from signalyze.storage.repositories import (
    fetch_reported_outcome,
    upsert_follow_up,
    upsert_link,
    upsert_messages,
    upsert_signal,
)


def _seed_signal_with_events(
    db: Database,
    *,
    follow_ups: list[FollowUpEvent],
    direction: Direction = Direction.BUY,
) -> Signal:
    msg_signal = Message(
        message_uid="g:1",
        group_id="g",
        message_id=1,
        timestamp_utc="2026-01-17T10:00:00Z",
        text="signal",
        ingested_at="2026-01-17T10:00:00Z",
        ingest_method="csv_backfill",
    )
    follow_messages = [
        Message(
            message_uid=ev.message_uid,
            group_id="g",
            message_id=int(ev.message_uid.split(":")[1]),
            timestamp_utc=ev.timestamp_utc,
            text="follow",
            ingested_at=ev.timestamp_utc,
            ingest_method="csv_backfill",
        )
        for ev in follow_ups
    ]
    with db.transaction() as conn:
        upsert_messages(conn, [msg_signal, *follow_messages])

    signal = Signal(
        signal_id="g:1",
        message_uid="g:1",
        group_id="g",
        timestamp_utc=msg_signal.timestamp_utc,
        direction=direction,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0, 4720.0, 4730.0],
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at="2026-01-17T10:00:01Z",
    )
    with db.transaction() as conn:
        upsert_signal(conn, signal)
        for event in follow_ups:
            upsert_follow_up(conn, event)
            upsert_link(
                conn,
                SignalFollowUpLink(
                    link_id=f"{event.follow_up_id}->{signal.signal_id}",
                    follow_up_id=event.follow_up_id,
                    signal_id=signal.signal_id,
                    link_method=LinkMethod.REPLY_TO,
                    link_confidence=0.95,
                    reasons=["test"],
                    linked_at="2026-01-17T11:00:00Z",
                    linker_version="v0.1",
                ),
            )
    return signal


def _event(
    message_id: int,
    event_type: FollowUpEventType,
    timestamp: str,
    *,
    tp_index: int | None = None,
    pips: float | None = None,
) -> FollowUpEvent:
    return FollowUpEvent(
        follow_up_id=f"g:{message_id}",
        message_uid=f"g:{message_id}",
        group_id="g",
        timestamp_utc=timestamp,
        event_type=event_type,
        tp_index=tp_index,
        claimed_pips=pips,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parse_reasons=[],
        parsed_at=timestamp,
    )


def test_no_follow_ups_yields_no_report(tmp_db: Database) -> None:
    _seed_signal_with_events(tmp_db, follow_ups=[])
    stats = compute_reported_outcomes(db=tmp_db)
    outcome = fetch_reported_outcome(tmp_db.conn, "g:1")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.NO_REPORT
    assert stats.outcomes_written == 1


def test_tp_only_yields_win_with_max_tp(tmp_db: Database) -> None:
    events = [
        _event(2, FollowUpEventType.TP_HIT, "2026-01-17T10:30:00Z", tp_index=1, pips=10),
        _event(3, FollowUpEventType.TP_HIT, "2026-01-17T11:00:00Z", tp_index=2, pips=20),
    ]
    _seed_signal_with_events(tmp_db, follow_ups=events)
    compute_reported_outcomes(db=tmp_db)
    outcome = fetch_reported_outcome(tmp_db.conn, "g:1")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.WIN
    assert outcome.max_tp_hit == 2
    assert outcome.claimed_pips == 30.0


def test_sl_before_tp_yields_loss(tmp_db: Database) -> None:
    events = [
        _event(2, FollowUpEventType.SL_HIT, "2026-01-17T10:30:00Z", pips=-50),
        _event(3, FollowUpEventType.TP_HIT, "2026-01-17T11:00:00Z", tp_index=1),
    ]
    _seed_signal_with_events(tmp_db, follow_ups=events)
    compute_reported_outcomes(db=tmp_db)
    outcome = fetch_reported_outcome(tmp_db.conn, "g:1")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.LOSS


def test_cancel_yields_no_report(tmp_db: Database) -> None:
    events = [
        _event(2, FollowUpEventType.CANCEL, "2026-01-17T10:05:00Z"),
        _event(3, FollowUpEventType.TP_HIT, "2026-01-17T10:30:00Z", tp_index=1),
    ]
    _seed_signal_with_events(tmp_db, follow_ups=events)
    compute_reported_outcomes(db=tmp_db)
    outcome = fetch_reported_outcome(tmp_db.conn, "g:1")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.NO_REPORT


def test_only_updates_yields_open(tmp_db: Database) -> None:
    events = [_event(2, FollowUpEventType.UPDATE, "2026-01-17T10:30:00Z", pips=15)]
    _seed_signal_with_events(tmp_db, follow_ups=events)
    compute_reported_outcomes(db=tmp_db)
    outcome = fetch_reported_outcome(tmp_db.conn, "g:1")
    assert outcome is not None
    assert outcome.final_state == OutcomeState.OPEN
