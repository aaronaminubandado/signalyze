"""Repository helpers: typed read/write of domain models against the SQLite store."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from signalyze.domain import (
    ActualOutcome,
    FollowUpEvent,
    FollowUpEventType,
    LinkMethod,
    MarketBar,
    Message,
    MessageClass,
    MessageClassification,
    OutcomeState,
    QualityFlag,
    ReportedOutcome,
    Signal,
    SignalFollowUpLink,
    WinPolicy,
)
from signalyze.domain.signals import Direction


def upsert_messages(conn: sqlite3.Connection, messages: Iterable[Message]) -> int:
    """Insert messages, ignoring duplicates by `(group_id, message_id)`."""
    rows = [
        (
            m.message_uid,
            m.group_id,
            m.message_id,
            m.timestamp_utc,
            m.sender_id,
            m.text,
            m.reply_to_msg_id,
            m.views,
            m.forwards,
            m.reply_count,
            m.ingested_at,
            m.ingest_method,
        )
        for m in messages
    ]
    cursor = conn.executemany(
        """
        INSERT OR IGNORE INTO messages (
            message_uid, group_id, message_id, timestamp_utc, sender_id, text,
            reply_to_msg_id, views, forwards, reply_count, ingested_at, ingest_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cursor.rowcount


def fetch_messages_by_class(
    conn: sqlite3.Connection,
    message_class: MessageClass,
    *,
    group_id: str | None = None,
    limit: int | None = None,
) -> list[Message]:
    """Return messages with the given classification."""
    sql = (
        "SELECT m.* FROM messages m "
        "JOIN message_classifications c ON c.message_uid = m.message_uid "
        "WHERE c.class = ?"
    )
    params: list[Any] = [message_class.value]
    if group_id is not None:
        sql += " AND m.group_id = ?"
        params.append(group_id)
    sql += " ORDER BY m.timestamp_utc"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    return [_message_from_row(row) for row in conn.execute(sql, params)]


def upsert_classification(
    conn: sqlite3.Connection,
    classification: MessageClassification,
) -> None:
    conn.execute(
        """
        INSERT INTO message_classifications (
            message_uid, class, confidence, method, reasons,
            classifier_version, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_uid) DO UPDATE SET
            class = excluded.class,
            confidence = excluded.confidence,
            method = excluded.method,
            reasons = excluded.reasons,
            classifier_version = excluded.classifier_version,
            classified_at = excluded.classified_at
        """,
        (
            classification.message_uid,
            classification.message_class.value,
            classification.confidence,
            classification.method,
            json.dumps(classification.reasons),
            classification.classifier_version,
            classification.classified_at,
        ),
    )


def upsert_signal(conn: sqlite3.Connection, signal: Signal) -> None:
    conn.execute(
        """
        INSERT INTO signals (
            signal_id, message_uid, group_id, timestamp_utc, direction, instrument,
            entry, entry_low, entry_high, stop_loss, take_profits,
            quality_flag, parse_method, parse_confidence, parse_version,
            parse_reasons, parsed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(signal_id) DO UPDATE SET
            direction = excluded.direction,
            instrument = excluded.instrument,
            entry = excluded.entry,
            entry_low = excluded.entry_low,
            entry_high = excluded.entry_high,
            stop_loss = excluded.stop_loss,
            take_profits = excluded.take_profits,
            quality_flag = excluded.quality_flag,
            parse_method = excluded.parse_method,
            parse_confidence = excluded.parse_confidence,
            parse_version = excluded.parse_version,
            parse_reasons = excluded.parse_reasons,
            parsed_at = excluded.parsed_at
        """,
        (
            signal.signal_id,
            signal.message_uid,
            signal.group_id,
            signal.timestamp_utc,
            signal.direction.value,
            signal.instrument,
            signal.entry,
            signal.entry_low,
            signal.entry_high,
            signal.stop_loss,
            json.dumps(signal.take_profits),
            signal.quality_flag.value,
            signal.parse_method,
            signal.parse_confidence,
            signal.parse_version,
            json.dumps(signal.parse_reasons),
            signal.parsed_at,
        ),
    )


def fetch_signals(
    conn: sqlite3.Connection,
    *,
    group_id: str | None = None,
) -> list[Signal]:
    sql = "SELECT * FROM signals"
    params: list[Any] = []
    if group_id is not None:
        sql += " WHERE group_id = ?"
        params.append(group_id)
    sql += " ORDER BY timestamp_utc"
    return [_signal_from_row(row) for row in conn.execute(sql, params)]


def upsert_follow_up(conn: sqlite3.Connection, event: FollowUpEvent) -> None:
    conn.execute(
        """
        INSERT INTO follow_ups (
            follow_up_id, message_uid, group_id, timestamp_utc, event_type,
            tp_index, claimed_price, claimed_pips, new_stop_loss,
            parse_method, parse_confidence, parse_version, parse_reasons, parsed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(follow_up_id) DO UPDATE SET
            event_type = excluded.event_type,
            tp_index = excluded.tp_index,
            claimed_price = excluded.claimed_price,
            claimed_pips = excluded.claimed_pips,
            new_stop_loss = excluded.new_stop_loss,
            parse_method = excluded.parse_method,
            parse_confidence = excluded.parse_confidence,
            parse_version = excluded.parse_version,
            parse_reasons = excluded.parse_reasons,
            parsed_at = excluded.parsed_at
        """,
        (
            event.follow_up_id,
            event.message_uid,
            event.group_id,
            event.timestamp_utc,
            event.event_type.value,
            event.tp_index,
            event.claimed_price,
            event.claimed_pips,
            event.new_stop_loss,
            event.parse_method,
            event.parse_confidence,
            event.parse_version,
            json.dumps(event.parse_reasons),
            event.parsed_at,
        ),
    )


def fetch_follow_ups(
    conn: sqlite3.Connection,
    *,
    group_id: str | None = None,
) -> list[FollowUpEvent]:
    sql = "SELECT * FROM follow_ups"
    params: list[Any] = []
    if group_id is not None:
        sql += " WHERE group_id = ?"
        params.append(group_id)
    sql += " ORDER BY timestamp_utc"
    return [_follow_up_from_row(row) for row in conn.execute(sql, params)]


def upsert_link(conn: sqlite3.Connection, link: SignalFollowUpLink) -> None:
    conn.execute(
        """
        INSERT INTO signal_follow_up_links (
            link_id, follow_up_id, signal_id, link_method, link_confidence,
            reasons, linked_at, linker_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(link_id) DO UPDATE SET
            link_method = excluded.link_method,
            link_confidence = excluded.link_confidence,
            reasons = excluded.reasons,
            linked_at = excluded.linked_at,
            linker_version = excluded.linker_version
        """,
        (
            link.link_id,
            link.follow_up_id,
            link.signal_id,
            link.link_method.value,
            link.link_confidence,
            json.dumps(link.reasons),
            link.linked_at,
            link.linker_version,
        ),
    )


def fetch_links_for_signal(
    conn: sqlite3.Connection,
    signal_id: str,
) -> list[SignalFollowUpLink]:
    rows = conn.execute(
        "SELECT * FROM signal_follow_up_links WHERE signal_id = ?",
        (signal_id,),
    ).fetchall()
    return [_link_from_row(row) for row in rows]


def fetch_links(
    conn: sqlite3.Connection,
    *,
    min_confidence: float = 0.0,
    group_id: str | None = None,
) -> list[SignalFollowUpLink]:
    """Return all links at or above `min_confidence`."""
    sql = "SELECT l.* FROM signal_follow_up_links l"
    params: list[Any] = [min_confidence]
    if group_id is not None:
        sql += (
            " JOIN signals s ON s.signal_id = l.signal_id "
            "WHERE l.link_confidence >= ? AND s.group_id = ?"
        )
        params.append(group_id)
    else:
        sql += " WHERE l.link_confidence >= ?"
    sql += " ORDER BY l.signal_id, l.linked_at"
    return [_link_from_row(row) for row in conn.execute(sql, params)]


def upsert_reported_outcome(conn: sqlite3.Connection, outcome: ReportedOutcome) -> None:
    conn.execute(
        """
        INSERT INTO reported_outcomes (
            signal_id, final_state, max_tp_hit, claimed_pips, closed_at_utc,
            source_follow_up_count, computed_at, computed_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(signal_id) DO UPDATE SET
            final_state = excluded.final_state,
            max_tp_hit = excluded.max_tp_hit,
            claimed_pips = excluded.claimed_pips,
            closed_at_utc = excluded.closed_at_utc,
            source_follow_up_count = excluded.source_follow_up_count,
            computed_at = excluded.computed_at,
            computed_version = excluded.computed_version
        """,
        (
            outcome.signal_id,
            outcome.final_state.value,
            outcome.max_tp_hit,
            outcome.claimed_pips,
            outcome.closed_at_utc,
            outcome.source_follow_up_count,
            outcome.computed_at,
            outcome.computed_version,
        ),
    )


def upsert_actual_outcome(conn: sqlite3.Connection, outcome: ActualOutcome) -> None:
    conn.execute(
        """
        INSERT INTO actual_outcomes (
            signal_id, final_state, first_touch_event, first_touch_price,
            first_touch_at_utc, realized_rr, realized_pips, bars_to_outcome,
            win_policy, max_holding_hours, default_sl_policy,
            computed_at, computed_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(signal_id) DO UPDATE SET
            final_state = excluded.final_state,
            first_touch_event = excluded.first_touch_event,
            first_touch_price = excluded.first_touch_price,
            first_touch_at_utc = excluded.first_touch_at_utc,
            realized_rr = excluded.realized_rr,
            realized_pips = excluded.realized_pips,
            bars_to_outcome = excluded.bars_to_outcome,
            win_policy = excluded.win_policy,
            max_holding_hours = excluded.max_holding_hours,
            default_sl_policy = excluded.default_sl_policy,
            computed_at = excluded.computed_at,
            computed_version = excluded.computed_version
        """,
        (
            outcome.signal_id,
            outcome.final_state.value,
            outcome.first_touch_event,
            outcome.first_touch_price,
            outcome.first_touch_at_utc,
            outcome.realized_rr,
            outcome.realized_pips,
            outcome.bars_to_outcome,
            outcome.win_policy.value,
            outcome.max_holding_hours,
            outcome.default_sl_policy,
            outcome.computed_at,
            outcome.computed_version,
        ),
    )


def upsert_market_bars(conn: sqlite3.Connection, bars: Iterable[MarketBar]) -> int:
    rows = [
        (
            bar.bar_id,
            bar.instrument,
            bar.interval,
            bar.timestamp_utc,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            bar.provider,
            bar.fetched_at,
        )
        for bar in bars
    ]
    cursor = conn.executemany(
        """
        INSERT OR REPLACE INTO market_bars (
            bar_id, instrument, interval, timestamp_utc, open, high, low, close,
            volume, provider, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cursor.rowcount


def fetch_market_bars(
    conn: sqlite3.Connection,
    *,
    instrument: str,
    interval: str,
    start_utc: str,
    end_utc: str,
) -> list[MarketBar]:
    rows = conn.execute(
        """
        SELECT * FROM market_bars
        WHERE instrument = ? AND interval = ?
          AND timestamp_utc >= ? AND timestamp_utc <= ?
        ORDER BY timestamp_utc
        """,
        (instrument, interval, start_utc, end_utc),
    ).fetchall()
    return [_market_bar_from_row(row) for row in rows]


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        message_uid=row["message_uid"],
        group_id=row["group_id"],
        message_id=row["message_id"],
        timestamp_utc=row["timestamp_utc"],
        sender_id=row["sender_id"],
        text=row["text"] or "",
        reply_to_msg_id=row["reply_to_msg_id"],
        views=row["views"],
        forwards=row["forwards"],
        reply_count=row["reply_count"],
        ingested_at=row["ingested_at"],
        ingest_method=row["ingest_method"],
    )


def _signal_from_row(row: sqlite3.Row) -> Signal:
    return Signal(
        signal_id=row["signal_id"],
        message_uid=row["message_uid"],
        group_id=row["group_id"],
        timestamp_utc=row["timestamp_utc"],
        direction=Direction(row["direction"]),
        instrument=row["instrument"],
        entry=row["entry"],
        entry_low=row["entry_low"],
        entry_high=row["entry_high"],
        stop_loss=row["stop_loss"],
        take_profits=json.loads(row["take_profits"] or "[]"),
        quality_flag=QualityFlag(row["quality_flag"]),
        parse_method=row["parse_method"],
        parse_confidence=row["parse_confidence"],
        parse_version=row["parse_version"],
        parse_reasons=json.loads(row["parse_reasons"] or "[]"),
        parsed_at=row["parsed_at"],
    )


def _follow_up_from_row(row: sqlite3.Row) -> FollowUpEvent:
    return FollowUpEvent(
        follow_up_id=row["follow_up_id"],
        message_uid=row["message_uid"],
        group_id=row["group_id"],
        timestamp_utc=row["timestamp_utc"],
        event_type=FollowUpEventType(row["event_type"]),
        tp_index=row["tp_index"],
        claimed_price=row["claimed_price"],
        claimed_pips=row["claimed_pips"],
        new_stop_loss=row["new_stop_loss"],
        parse_method=row["parse_method"],
        parse_confidence=row["parse_confidence"],
        parse_version=row["parse_version"],
        parse_reasons=json.loads(row["parse_reasons"] or "[]"),
        parsed_at=row["parsed_at"],
    )


def _link_from_row(row: sqlite3.Row) -> SignalFollowUpLink:
    return SignalFollowUpLink(
        link_id=row["link_id"],
        follow_up_id=row["follow_up_id"],
        signal_id=row["signal_id"],
        link_method=LinkMethod(row["link_method"]),
        link_confidence=row["link_confidence"],
        reasons=json.loads(row["reasons"] or "[]"),
        linked_at=row["linked_at"],
        linker_version=row["linker_version"],
    )


def _market_bar_from_row(row: sqlite3.Row) -> MarketBar:
    return MarketBar(
        instrument=row["instrument"],
        interval=row["interval"],
        timestamp_utc=row["timestamp_utc"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        provider=row["provider"],
        fetched_at=row["fetched_at"],
    )


def fetch_reported_outcome(conn: sqlite3.Connection, signal_id: str) -> ReportedOutcome | None:
    row = conn.execute(
        "SELECT * FROM reported_outcomes WHERE signal_id = ?", (signal_id,)
    ).fetchone()
    if row is None:
        return None
    return ReportedOutcome(
        signal_id=row["signal_id"],
        final_state=OutcomeState(row["final_state"]),
        max_tp_hit=row["max_tp_hit"],
        claimed_pips=row["claimed_pips"],
        closed_at_utc=row["closed_at_utc"],
        source_follow_up_count=row["source_follow_up_count"],
        computed_at=row["computed_at"],
        computed_version=row["computed_version"],
    )


def fetch_actual_outcome(conn: sqlite3.Connection, signal_id: str) -> ActualOutcome | None:
    row = conn.execute(
        "SELECT * FROM actual_outcomes WHERE signal_id = ?", (signal_id,)
    ).fetchone()
    if row is None:
        return None
    return ActualOutcome(
        signal_id=row["signal_id"],
        final_state=OutcomeState(row["final_state"]),
        first_touch_event=row["first_touch_event"],
        first_touch_price=row["first_touch_price"],
        first_touch_at_utc=row["first_touch_at_utc"],
        realized_rr=row["realized_rr"],
        realized_pips=row["realized_pips"],
        bars_to_outcome=row["bars_to_outcome"],
        win_policy=WinPolicy(row["win_policy"]),
        max_holding_hours=row["max_holding_hours"],
        default_sl_policy=row["default_sl_policy"],
        computed_at=row["computed_at"],
        computed_version=row["computed_version"],
    )
