"""Per-group, per-TP-level hit-rate breakdown.

Quantifies *how deep into the take-profit ladder* a channel's signals actually
get (according to the channel's own follow-ups). Complements the existing
`compute_group_metrics` win-rate view, which only tells you whether *any* TP
was reached and therefore saturates near 100% on most published channels.

Denominator semantics (intentionally strict to avoid penalising quiet channels
or channels that publish fewer TPs):

    denom_N = signals where len(take_profits) >= N
              AND reported_outcomes.final_state != 'NO_REPORT'
    num_N   = signals counted in denom_N with max_tp_hit >= N
    hit_rate_N = num_N / denom_N   (None if denom_N == 0)
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from signalyze.domain import OutcomeState
from signalyze.storage import Database


@dataclass(frozen=True)
class TpLevelStat:
    """Hit-rate statistic for a single TP level inside a group."""

    level: int
    denom: int
    hits: int
    hit_rate: float | None


@dataclass(frozen=True)
class GroupTpDepth:
    """Per-group TP-depth breakdown.

    `tp_levels` is dense and always covers `1..max_tp_level` even when some
    intermediate levels have zero denominators; this keeps downstream rendering
    code simple (it can just index into the list).
    """

    group_id: str
    n_signals: int
    n_reported: int
    no_report_rate: float | None
    sl_hit_rate: float | None
    max_tp_level: int
    tp_levels: list[TpLevelStat]

    def level(self, n: int) -> TpLevelStat | None:
        """Return the stats for TP level `n` (1-indexed), or `None` if unobserved."""
        if 1 <= n <= len(self.tp_levels):
            return self.tp_levels[n - 1]
        return None


def iter_tp_depth(
    *,
    db: Database,
    start_utc: str | None = None,
    end_utc: str | None = None,
) -> Iterable[GroupTpDepth]:
    """Yield TP-depth breakdowns for every group with at least one signal."""
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
        yield compute_tp_depth(
            db=db,
            group_id=row["group_id"],
            start_utc=start_utc,
            end_utc=end_utc,
        )


def compute_tp_depth(
    *,
    db: Database,
    group_id: str,
    start_utc: str | None = None,
    end_utc: str | None = None,
) -> GroupTpDepth:
    """Compute the TP-depth breakdown for a single group."""
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
               s.take_profits,
               r.final_state AS reported_state,
               r.max_tp_hit  AS max_tp_hit
        FROM signals s
        LEFT JOIN reported_outcomes r ON r.signal_id = s.signal_id
        {where_sql}
        """,
        params,
    ).fetchall()

    n_signals = len(rows)
    no_report_state = OutcomeState.NO_REPORT.value
    loss_state = OutcomeState.LOSS.value

    n_reported = 0
    n_no_report = 0
    n_loss = 0
    max_tp_level = 0

    # First pass: figure out the max TP level advertised by this group and
    # compute the "n_reported" count we'll need for the SL rate.
    parsed_take_profits: list[list[float]] = []
    for row in rows:
        tps = _parse_take_profits(row["take_profits"])
        parsed_take_profits.append(tps)
        max_tp_level = max(max_tp_level, len(tps))

        state = row["reported_state"]
        if state == no_report_state or state is None:
            n_no_report += 1
        else:
            n_reported += 1
            if state == loss_state:
                n_loss += 1

    # Second pass: tally per-level denominators and hits.
    denoms = [0] * max_tp_level
    hits = [0] * max_tp_level
    for row, tps in zip(rows, parsed_take_profits, strict=True):
        state = row["reported_state"]
        if state == no_report_state or state is None:
            continue
        max_tp_hit = row["max_tp_hit"]
        for level_idx in range(len(tps)):
            denoms[level_idx] += 1
            if max_tp_hit is not None and max_tp_hit >= level_idx + 1:
                hits[level_idx] += 1

    tp_levels = [
        TpLevelStat(
            level=i + 1,
            denom=denoms[i],
            hits=hits[i],
            hit_rate=_safe_div(hits[i], denoms[i]),
        )
        for i in range(max_tp_level)
    ]

    return GroupTpDepth(
        group_id=group_id,
        n_signals=n_signals,
        n_reported=n_reported,
        no_report_rate=_safe_div(n_no_report, n_signals),
        sl_hit_rate=_safe_div(n_loss, n_reported),
        max_tp_level=max_tp_level,
        tp_levels=tp_levels,
    )


def _parse_take_profits(value: object) -> list[float]:
    if value is None:
        return []
    if isinstance(value, list):
        return [float(v) for v in value]
    if isinstance(value, str):
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [float(v) for v in parsed]
    return []


def _safe_div(num: int, denom: int) -> float | None:
    if denom == 0:
        return None
    return round(num / denom, 4)
