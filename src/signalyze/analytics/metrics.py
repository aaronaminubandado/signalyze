"""Per-group / per-period performance metrics.

All metrics are *segmented* so a single function can answer questions like
"what's the actual win rate for group X in March?". We deliberately keep the
shape narrow (a single dataclass) and return iterables, so the dashboard can
remain a thin presentation layer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean

from signalyze.domain import OutcomeState
from signalyze.storage import Database


@dataclass(frozen=True)
class GroupMetrics:
    group_id: str
    n_signals: int

    reported_wins: int
    reported_losses: int
    reported_decided: int
    reported_win_rate: float | None

    actual_wins: int
    actual_losses: int
    actual_decided: int
    actual_win_rate: float | None

    avg_realized_pips: float | None
    avg_realized_rr: float | None
    median_bars_to_outcome: float | None
    ambiguous_bars: int
    insufficient_data: int

    @property
    def win_rate_gap(self) -> float | None:
        if self.reported_win_rate is None or self.actual_win_rate is None:
            return None
        return round(self.reported_win_rate - self.actual_win_rate, 4)


def iter_group_metrics(
    *,
    db: Database,
    start_utc: str | None = None,
    end_utc: str | None = None,
    min_link_confidence: float = 0.6,
) -> Iterable[GroupMetrics]:
    """Yield metrics for every group that has at least one signal in the given window."""
    _ = min_link_confidence  # currently unused: reported outcomes already filter on link conf.
    where = []
    params: list[object] = []
    if start_utc is not None:
        where.append("s.timestamp_utc >= ?")
        params.append(start_utc)
    if end_utc is not None:
        where.append("s.timestamp_utc <= ?")
        params.append(end_utc)
    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    group_rows = db.conn.execute(
        f"SELECT DISTINCT s.group_id FROM signals s{where_clause}",
        params,
    ).fetchall()

    for row in group_rows:
        yield compute_group_metrics(
            db=db,
            group_id=row["group_id"],
            start_utc=start_utc,
            end_utc=end_utc,
        )


def compute_group_metrics(
    *,
    db: Database,
    group_id: str,
    start_utc: str | None = None,
    end_utc: str | None = None,
) -> GroupMetrics:
    conditions = ["s.group_id = ?"]
    params: list[object] = [group_id]
    if start_utc is not None:
        conditions.append("s.timestamp_utc >= ?")
        params.append(start_utc)
    if end_utc is not None:
        conditions.append("s.timestamp_utc <= ?")
        params.append(end_utc)
    where_sql = " WHERE " + " AND ".join(conditions)

    rows = db.conn.execute(
        f"""
        SELECT s.signal_id,
               r.final_state AS reported_state,
               a.final_state AS actual_state,
               a.realized_pips AS actual_pips,
               a.realized_rr AS actual_rr,
               a.bars_to_outcome AS bars_to_outcome,
               a.first_touch_event AS first_touch_event
        FROM signals s
        LEFT JOIN reported_outcomes r ON r.signal_id = s.signal_id
        LEFT JOIN actual_outcomes a ON a.signal_id = s.signal_id
        {where_sql}
        """,
        params,
    ).fetchall()

    reported_wins = sum(1 for r in rows if r["reported_state"] == OutcomeState.WIN.value)
    reported_losses = sum(1 for r in rows if r["reported_state"] == OutcomeState.LOSS.value)
    actual_wins = sum(1 for r in rows if r["actual_state"] == OutcomeState.WIN.value)
    actual_losses = sum(1 for r in rows if r["actual_state"] == OutcomeState.LOSS.value)

    actual_pips_values = [r["actual_pips"] for r in rows if r["actual_pips"] is not None]
    actual_rr_values = [r["actual_rr"] for r in rows if r["actual_rr"] is not None]
    bars_values = sorted(r["bars_to_outcome"] for r in rows if r["bars_to_outcome"] is not None)

    ambiguous_bars = sum(1 for r in rows if r["actual_state"] == OutcomeState.AMBIGUOUS.value)
    insufficient = sum(
        1 for r in rows if r["actual_state"] == OutcomeState.INSUFFICIENT_DATA.value
    )

    reported_decided = reported_wins + reported_losses
    actual_decided = actual_wins + actual_losses

    return GroupMetrics(
        group_id=group_id,
        n_signals=len(rows),
        reported_wins=reported_wins,
        reported_losses=reported_losses,
        reported_decided=reported_decided,
        reported_win_rate=_safe_div(reported_wins, reported_decided),
        actual_wins=actual_wins,
        actual_losses=actual_losses,
        actual_decided=actual_decided,
        actual_win_rate=_safe_div(actual_wins, actual_decided),
        avg_realized_pips=_safe_mean(actual_pips_values),
        avg_realized_rr=_safe_mean(actual_rr_values),
        median_bars_to_outcome=_median(bars_values),
        ambiguous_bars=ambiguous_bars,
        insufficient_data=insufficient,
    )


def _safe_div(num: int, denom: int) -> float | None:
    if denom == 0:
        return None
    return round(num / denom, 4)


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(mean(values), 3)


def _median(sorted_values: list[float]) -> float | None:
    if not sorted_values:
        return None
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_values[mid])
    return round((sorted_values[mid - 1] + sorted_values[mid]) / 2.0, 3)
