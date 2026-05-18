"""Unit tests for the tiered linker."""

from __future__ import annotations

from signalyze.domain import (
    Direction,
    FollowUpEvent,
    FollowUpEventType,
    LinkMethod,
    Message,
    QualityFlag,
    Signal,
)
from signalyze.link import Linker
from signalyze.storage import Database
from signalyze.storage.repositories import (
    upsert_follow_up,
    upsert_messages,
    upsert_signal,
)


def _seed(db: Database) -> tuple[Signal, FollowUpEvent]:
    msg_signal = Message(
        message_uid="g:1",
        group_id="g",
        message_id=1,
        timestamp_utc="2026-01-17T10:00:00Z",
        text="BUY",
        ingested_at="2026-01-17T10:00:00Z",
        ingest_method="csv_backfill",
    )
    msg_follow = Message(
        message_uid="g:2",
        group_id="g",
        message_id=2,
        timestamp_utc="2026-01-17T10:30:00Z",
        text="TP1 hit",
        reply_to_msg_id=1,
        ingested_at="2026-01-17T10:30:00Z",
        ingest_method="csv_backfill",
    )
    with db.transaction() as conn:
        upsert_messages(conn, [msg_signal, msg_follow])

    signal = Signal(
        signal_id="g:1",
        message_uid="g:1",
        group_id="g",
        timestamp_utc=msg_signal.timestamp_utc,
        direction=Direction.BUY,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0, 4720.0],
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.95,
        parse_version="v0.1",
        parsed_at="2026-01-17T10:00:01Z",
    )
    follow_up = FollowUpEvent(
        follow_up_id="g:2",
        message_uid="g:2",
        group_id="g",
        timestamp_utc=msg_follow.timestamp_utc,
        event_type=FollowUpEventType.TP_HIT,
        tp_index=1,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at="2026-01-17T10:30:01Z",
    )
    with db.transaction() as conn:
        upsert_signal(conn, signal)
        upsert_follow_up(conn, follow_up)
    return signal, follow_up


def test_reply_to_links_with_high_confidence(tmp_db: Database) -> None:
    _, _ = _seed(tmp_db)
    stats = Linker().run(tmp_db, group_id="g")
    assert stats.linked == 1
    assert stats.by_method[LinkMethod.REPLY_TO.value] == 1

    link_row = tmp_db.conn.execute("SELECT * FROM signal_follow_up_links").fetchone()
    assert link_row["signal_id"] == "g:1"
    assert link_row["link_confidence"] >= 0.95


def test_temporal_fallback_when_no_reply(tmp_db: Database) -> None:
    msg_signal = Message(
        message_uid="g:10",
        group_id="g",
        message_id=10,
        timestamp_utc="2026-01-17T09:00:00Z",
        text="SELL",
        ingested_at="2026-01-17T09:00:00Z",
        ingest_method="csv_backfill",
    )
    msg_follow = Message(
        message_uid="g:11",
        group_id="g",
        message_id=11,
        timestamp_utc="2026-01-17T09:30:00Z",
        text="TP1 hit",
        reply_to_msg_id=None,
        ingested_at="2026-01-17T09:30:00Z",
        ingest_method="csv_backfill",
    )
    with tmp_db.transaction() as conn:
        upsert_messages(conn, [msg_signal, msg_follow])

    signal = Signal(
        signal_id="g:10",
        message_uid="g:10",
        group_id="g",
        timestamp_utc=msg_signal.timestamp_utc,
        direction=Direction.SELL,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4710.0,
        take_profits=[4695.0, 4690.0],
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at="2026-01-17T09:00:01Z",
    )
    follow_up = FollowUpEvent(
        follow_up_id="g:11",
        message_uid="g:11",
        group_id="g",
        timestamp_utc=msg_follow.timestamp_utc,
        event_type=FollowUpEventType.TP_HIT,
        tp_index=1,
        claimed_price=4695.0,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at="2026-01-17T09:30:01Z",
    )
    with tmp_db.transaction() as conn:
        upsert_signal(conn, signal)
        upsert_follow_up(conn, follow_up)

    stats = Linker().run(tmp_db, group_id="g")
    assert stats.linked == 1
    assert stats.by_method.get(LinkMethod.TEMPORAL_NUMERIC.value) == 1


def test_outside_window_is_unlinked(tmp_db: Database) -> None:
    msg_signal = Message(
        message_uid="g:20",
        group_id="g",
        message_id=20,
        timestamp_utc="2026-01-17T09:00:00Z",
        text="BUY",
        ingested_at="2026-01-17T09:00:00Z",
        ingest_method="csv_backfill",
    )
    # 30 days later -> beyond active window.
    msg_follow = Message(
        message_uid="g:21",
        group_id="g",
        message_id=21,
        timestamp_utc="2026-02-17T09:30:00Z",
        text="TP1 hit",
        ingested_at="2026-02-17T09:30:00Z",
        ingest_method="csv_backfill",
    )
    with tmp_db.transaction() as conn:
        upsert_messages(conn, [msg_signal, msg_follow])

    signal = Signal(
        signal_id="g:20",
        message_uid="g:20",
        group_id="g",
        timestamp_utc=msg_signal.timestamp_utc,
        direction=Direction.BUY,
        instrument="XAUUSD",
        entry=4700.0,
        stop_loss=4690.0,
        take_profits=[4710.0],
        quality_flag=QualityFlag.COMPLETE,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at="2026-01-17T09:00:01Z",
    )
    follow_up = FollowUpEvent(
        follow_up_id="g:21",
        message_uid="g:21",
        group_id="g",
        timestamp_utc=msg_follow.timestamp_utc,
        event_type=FollowUpEventType.TP_HIT,
        tp_index=1,
        parse_method="rules",
        parse_confidence=0.9,
        parse_version="v0.1",
        parsed_at="2026-02-17T09:30:01Z",
    )
    with tmp_db.transaction() as conn:
        upsert_signal(conn, signal)
        upsert_follow_up(conn, follow_up)

    stats = Linker().run(tmp_db, group_id="g")
    assert stats.linked == 0
    assert stats.unlinked == 1
