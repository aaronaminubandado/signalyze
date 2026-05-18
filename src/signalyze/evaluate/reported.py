"""Aggregate linked follow-ups into per-signal `ReportedOutcome` rows.

State derivation (in order):
    - CANCEL event present -> NO_REPORT (the trade never happened).
    - SL_HIT before any TP_HIT -> LOSS.
    - At least one TP_HIT -> WIN (record `max_tp_hit`).
    - MANUAL_CLOSE only -> BREAK_EVEN (we don't have enough info to call win/loss).
    - BE_MOVED / SL_MOVED / UPDATE only -> OPEN.
    - No linked follow-ups -> NO_REPORT.
"""

from __future__ import annotations

from dataclasses import dataclass

from signalyze.config import Settings, get_settings
from signalyze.domain import (
    FollowUpEvent,
    FollowUpEventType,
    OutcomeState,
    ReportedOutcome,
    Signal,
    SignalFollowUpLink,
)
from signalyze.storage import Database
from signalyze.storage.repositories import (
    fetch_follow_ups,
    fetch_links,
    fetch_signals,
    upsert_reported_outcome,
)
from signalyze.utils.logging import get_logger
from signalyze.utils.time import now_utc_iso

logger = get_logger("signalyze.evaluate.reported")


@dataclass
class ReportedStats:
    signals: int = 0
    outcomes_written: int = 0
    by_state: dict[str, int] | None = None
    min_link_confidence: float = 0.6

    def __post_init__(self) -> None:
        if self.by_state is None:
            self.by_state = {s.value: 0 for s in OutcomeState}


def compute_reported_outcomes(
    *,
    db: Database,
    min_link_confidence: float = 0.6,
    settings: Settings | None = None,
    group_id: str | None = None,
) -> ReportedStats:
    settings = settings or get_settings()
    stats = ReportedStats(min_link_confidence=min_link_confidence)
    version = settings.evaluate.evaluator_version

    signals = fetch_signals(db.conn, group_id=group_id)
    follow_ups = fetch_follow_ups(db.conn, group_id=group_id)
    follow_ups_by_id = {f.follow_up_id: f for f in follow_ups}

    links = fetch_links(db.conn, min_confidence=min_link_confidence, group_id=group_id)
    links_by_signal: dict[str, list[SignalFollowUpLink]] = {}
    for link in links:
        links_by_signal.setdefault(link.signal_id, []).append(link)

    stats.signals = len(signals)
    for signal in signals:
        related_links = links_by_signal.get(signal.signal_id, [])
        related_events = sorted(
            (follow_ups_by_id[link.follow_up_id] for link in related_links if link.follow_up_id in follow_ups_by_id),
            key=lambda e: e.timestamp_utc,
        )
        outcome = _derive_outcome(signal=signal, events=related_events, version=version)
        with db.transaction() as conn:
            upsert_reported_outcome(conn, outcome)
        stats.outcomes_written += 1
        assert stats.by_state is not None
        stats.by_state[outcome.final_state.value] += 1

    return stats


def _derive_outcome(
    *,
    signal: Signal,
    events: list[FollowUpEvent],
    version: str,
) -> ReportedOutcome:
    if not events:
        return ReportedOutcome(
            signal_id=signal.signal_id,
            final_state=OutcomeState.NO_REPORT,
            source_follow_up_count=0,
            computed_at=now_utc_iso(),
            computed_version=version,
        )

    cancel_event = next((e for e in events if e.event_type == FollowUpEventType.CANCEL), None)
    if cancel_event is not None:
        return ReportedOutcome(
            signal_id=signal.signal_id,
            final_state=OutcomeState.NO_REPORT,
            source_follow_up_count=len(events),
            closed_at_utc=cancel_event.timestamp_utc,
            computed_at=now_utc_iso(),
            computed_version=version,
        )

    sl_event = next((e for e in events if e.event_type == FollowUpEventType.SL_HIT), None)
    tp_events = [e for e in events if e.event_type == FollowUpEventType.TP_HIT]
    first_tp = tp_events[0] if tp_events else None

    if sl_event is not None and (first_tp is None or sl_event.timestamp_utc <= first_tp.timestamp_utc):
        return ReportedOutcome(
            signal_id=signal.signal_id,
            final_state=OutcomeState.LOSS,
            claimed_pips=sl_event.claimed_pips,
            closed_at_utc=sl_event.timestamp_utc,
            source_follow_up_count=len(events),
            computed_at=now_utc_iso(),
            computed_version=version,
        )

    if tp_events:
        max_tp = _max_tp_index(tp_events)
        last_tp = tp_events[-1]
        total_pips = sum(e.claimed_pips for e in tp_events if e.claimed_pips is not None) or None
        return ReportedOutcome(
            signal_id=signal.signal_id,
            final_state=OutcomeState.WIN,
            max_tp_hit=max_tp,
            claimed_pips=total_pips,
            closed_at_utc=last_tp.timestamp_utc,
            source_follow_up_count=len(events),
            computed_at=now_utc_iso(),
            computed_version=version,
        )

    manual_close = next((e for e in events if e.event_type == FollowUpEventType.MANUAL_CLOSE), None)
    if manual_close is not None:
        return ReportedOutcome(
            signal_id=signal.signal_id,
            final_state=OutcomeState.BREAK_EVEN,
            claimed_pips=manual_close.claimed_pips,
            closed_at_utc=manual_close.timestamp_utc,
            source_follow_up_count=len(events),
            computed_at=now_utc_iso(),
            computed_version=version,
        )

    return ReportedOutcome(
        signal_id=signal.signal_id,
        final_state=OutcomeState.OPEN,
        source_follow_up_count=len(events),
        computed_at=now_utc_iso(),
        computed_version=version,
    )


def _max_tp_index(events: list[FollowUpEvent]) -> int | None:
    indices = [e.tp_index for e in events if e.tp_index is not None]
    if not indices:
        return None
    return max(indices)
