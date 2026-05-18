"""Tiered signal <-> follow-up linker.

Tiers (in order of preference):
    1. `reply_to_msg_id` matches an existing signal -> highest confidence.
    2. Numeric match: the follow-up cites a price (or TP index) consistent with
       a recent signal in the same group, within the active window.
    3. Recency: the most recent open signal in the same group/direction within
       the active window.
    4. LLM tiebreaker (optional): when tiers 2/3 produce >=2 candidates with
       near-identical scores, ask an LLM to pick.

Every link carries `link_method` and `link_confidence` so downstream stages can
filter weak links out of headline metrics.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from signalyze.config import Settings, get_settings
from signalyze.domain import (
    FollowUpEvent,
    LinkMethod,
    Signal,
    SignalFollowUpLink,
)
from signalyze.link.llm_tiebreak import llm_tiebreak
from signalyze.llm import LLMClient
from signalyze.storage import Database
from signalyze.storage.repositories import (
    fetch_follow_ups,
    fetch_signals,
    upsert_link,
)
from signalyze.utils.logging import get_logger
from signalyze.utils.time import now_utc_iso, parse_utc

logger = get_logger("signalyze.link.linker")


@dataclass
class LinkStats:
    follow_ups: int = 0
    linked: int = 0
    unlinked: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    low_confidence: int = 0


@dataclass(frozen=True)
class LinkCandidate:
    """A scored candidate signal during tier 2/3 matching."""

    signal: Signal
    score: float
    method: LinkMethod
    reasons: list[str]


class Linker:
    """Stateful linker that runs through all unlinked follow-ups and writes link rows."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_client = llm_client
        self.version = self.settings.link.linker_version
        self.active_window_hours = self.settings.link.active_window_hours
        self.price_tolerance = self.settings.link.price_match_tolerance
        self.tiebreak_epsilon = self.settings.link.llm_tiebreak_epsilon

    def run(self, db: Database, *, group_id: str | None = None) -> LinkStats:
        """Build links for every follow-up and persist them."""
        signals = fetch_signals(db.conn, group_id=group_id)
        follow_ups = fetch_follow_ups(db.conn, group_id=group_id)
        reply_targets = _load_reply_targets(db, group_id=group_id)
        return self.link(
            db,
            signals=signals,
            follow_ups=follow_ups,
            reply_targets=reply_targets,
        )

    def link(
        self,
        db: Database,
        *,
        signals: list[Signal],
        follow_ups: list[FollowUpEvent],
        reply_targets: dict[str, int] | None = None,
    ) -> LinkStats:
        reply_targets = reply_targets or {}
        stats = LinkStats(follow_ups=len(follow_ups))

        # Index signals by message_id and group for fast lookup.
        by_message_id: dict[tuple[str, int], Signal] = {}
        by_group: dict[str, list[Signal]] = {}
        for signal in signals:
            mid = _extract_message_id(signal.message_uid)
            if mid is not None:
                by_message_id[(signal.group_id, mid)] = signal
            by_group.setdefault(signal.group_id, []).append(signal)
        for items in by_group.values():
            items.sort(key=lambda s: s.timestamp_utc)

        for event in follow_ups:
            link = self._link_one(
                event=event,
                by_message_id=by_message_id,
                by_group=by_group,
                reply_to=reply_targets.get(event.message_uid),
            )
            if link is None:
                stats.unlinked += 1
                continue

            with db.transaction() as conn:
                upsert_link(conn, link)

            stats.linked += 1
            stats.by_method[link.link_method.value] = (
                stats.by_method.get(link.link_method.value, 0) + 1
            )
            if link.link_confidence < 0.6:
                stats.low_confidence += 1

        return stats

    def _link_one(
        self,
        *,
        event: FollowUpEvent,
        by_message_id: dict[tuple[str, int], Signal],
        by_group: dict[str, list[Signal]],
        reply_to: int | None,
    ) -> SignalFollowUpLink | None:
        # Tier 1: reply_to_msg_id directly identifies the parent signal.
        if reply_to is not None:
            signal = by_message_id.get((event.group_id, reply_to))
            if signal is not None:
                return _make_link(
                    follow_up=event,
                    signal=signal,
                    method=LinkMethod.REPLY_TO,
                    confidence=0.98,
                    reasons=[f"reply_to={reply_to}"],
                    version=self.version,
                )

        # Build candidate list for tiers 2/3.
        candidates = self._collect_candidates(event=event, by_group=by_group)
        if not candidates:
            return None

        # Pick the best candidate; LLM tiebreak when top two are within epsilon.
        candidates.sort(key=lambda c: c.score, reverse=True)
        winner = candidates[0]
        if (
            len(candidates) >= 2
            and (candidates[0].score - candidates[1].score) <= self.tiebreak_epsilon
            and self.llm_client is not None
            and self.llm_client.is_available
        ):
            pick = llm_tiebreak(event=event, candidates=candidates[:5], client=self.llm_client)
            if pick is not None:
                winner = pick

        confidence = min(0.95, winner.score)
        return SignalFollowUpLink(
            link_id=f"{event.follow_up_id}->{winner.signal.signal_id}",
            follow_up_id=event.follow_up_id,
            signal_id=winner.signal.signal_id,
            link_method=winner.method,
            link_confidence=confidence,
            reasons=winner.reasons,
            linked_at=now_utc_iso(),
            linker_version=self.version,
        )

    def _collect_candidates(
        self,
        *,
        event: FollowUpEvent,
        by_group: dict[str, list[Signal]],
    ) -> list[LinkCandidate]:
        candidates: list[LinkCandidate] = []
        signals = by_group.get(event.group_id, [])
        if not signals:
            return candidates

        event_dt = parse_utc(event.timestamp_utc)
        window_seconds = self.active_window_hours * 3600.0

        for signal in signals:
            signal_dt = parse_utc(signal.timestamp_utc)
            delta = (event_dt - signal_dt).total_seconds()
            if delta < 0 or delta > window_seconds:
                continue

            reasons: list[str] = []
            score = 0.0

            # Tier 2: numeric match.
            if event.claimed_price is not None and self._price_matches_signal(
                event.claimed_price, signal
            ):
                score += 0.55
                reasons.append("price_match")

            if event.tp_index is not None and 1 <= event.tp_index <= len(signal.take_profits):
                score += 0.35
                reasons.append(f"tp_index_present_{event.tp_index}")

            # Tier 3: recency in window.
            recency_score = max(0.0, 1.0 - (delta / window_seconds))
            score += 0.30 * recency_score
            reasons.append(f"recency={recency_score:.2f}")

            method = (
                LinkMethod.TEMPORAL_NUMERIC
                if "price_match" in reasons or any("tp_index_present" in r for r in reasons)
                else LinkMethod.RECENT_OPEN
            )
            candidates.append(
                LinkCandidate(signal=signal, score=score, method=method, reasons=reasons)
            )

        return candidates

    def _price_matches_signal(self, price: float, signal: Signal) -> bool:
        candidates: list[float] = []
        if signal.entry is not None:
            candidates.append(signal.entry)
        if signal.entry_low is not None:
            candidates.append(signal.entry_low)
        if signal.entry_high is not None:
            candidates.append(signal.entry_high)
        if signal.stop_loss is not None:
            candidates.append(signal.stop_loss)
        candidates.extend(signal.take_profits)
        return any(abs(price - candidate) <= self.price_tolerance for candidate in candidates)


def _make_link(
    *,
    follow_up: FollowUpEvent,
    signal: Signal,
    method: LinkMethod,
    confidence: float,
    reasons: list[str],
    version: str,
) -> SignalFollowUpLink:
    return SignalFollowUpLink(
        link_id=f"{follow_up.follow_up_id}->{signal.signal_id}",
        follow_up_id=follow_up.follow_up_id,
        signal_id=signal.signal_id,
        link_method=method,
        link_confidence=confidence,
        reasons=reasons,
        linked_at=now_utc_iso(),
        linker_version=version,
    )


def _extract_message_id(message_uid: str) -> int | None:
    """Return the numeric message id portion of `group_id:message_id`."""
    parts = message_uid.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None


def _load_reply_targets(db: Database, *, group_id: str | None) -> dict[str, int]:
    """Map every follow-up's `message_uid` -> its `reply_to_msg_id` (when present)."""
    sql = (
        "SELECT m.message_uid, m.reply_to_msg_id FROM messages m "
        "JOIN follow_ups f ON f.message_uid = m.message_uid "
        "WHERE m.reply_to_msg_id IS NOT NULL"
    )
    params: list[object] = []
    if group_id is not None:
        sql += " AND m.group_id = ?"
        params.append(group_id)
    rows = db.conn.execute(sql, params).fetchall()
    return {row["message_uid"]: int(row["reply_to_msg_id"]) for row in rows}


def export_low_confidence_csv(
    db: Database,
    output_path: Path,
    *,
    threshold: float = 0.6,
) -> int:
    """Emit a CSV of links below `threshold` for manual review."""
    rows = db.conn.execute(
        """
        SELECT l.link_id, l.follow_up_id, l.signal_id, l.link_method, l.link_confidence,
               l.reasons, fm.text AS follow_up_text, sm.text AS signal_text
        FROM signal_follow_up_links l
        JOIN follow_ups fu ON fu.follow_up_id = l.follow_up_id
        JOIN signals s ON s.signal_id = l.signal_id
        JOIN messages fm ON fm.message_uid = fu.message_uid
        JOIN messages sm ON sm.message_uid = s.message_uid
        WHERE l.link_confidence < ?
        ORDER BY l.link_confidence ASC
        """,
        (threshold,),
    ).fetchall()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "link_id",
                "follow_up_id",
                "signal_id",
                "link_method",
                "link_confidence",
                "reasons",
                "follow_up_text",
                "signal_text",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["link_id"],
                    row["follow_up_id"],
                    row["signal_id"],
                    row["link_method"],
                    row["link_confidence"],
                    row["reasons"],
                    row["follow_up_text"],
                    row["signal_text"],
                ]
            )
    return len(rows)
