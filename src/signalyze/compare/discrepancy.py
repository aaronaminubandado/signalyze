"""Categorize reported vs actual outcome mismatches per signal.

Discrepancy categories (mutually exclusive, evaluated in order):

  - AGREES                 - reported state matches actual.
  - REPORTED_WIN_ACTUAL_LOSS - the channel claimed a win but the bars say SL hit first.
  - REPORTED_WIN_ACTUAL_OPEN - reported win, but TP was never actually touched.
  - REPORTED_LOSS_ACTUAL_WIN - reported loss, but a TP was touched before SL.
  - REPORTED_OPEN_ACTUAL_WIN - the channel went silent on a winning trade.
  - REPORTED_OPEN_ACTUAL_LOSS - silent loser; common censoring pattern.
  - REPORTED_NO_REPORT_ACTUAL_*  - the channel never reported any outcome.
  - AMBIGUOUS_BAR          - first-touched bar straddled SL and TP; cannot judge.
  - INSUFFICIENT_DATA      - no market bars; comparison is undefined.
  - UNKNOWN                - safety net.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from signalyze.domain import ActualOutcome, OutcomeState, ReportedOutcome
from signalyze.storage import Database


class DiscrepancyCategory(StrEnum):
    AGREES = "AGREES"
    REPORTED_WIN_ACTUAL_LOSS = "REPORTED_WIN_ACTUAL_LOSS"
    REPORTED_WIN_ACTUAL_OPEN = "REPORTED_WIN_ACTUAL_OPEN"
    REPORTED_LOSS_ACTUAL_WIN = "REPORTED_LOSS_ACTUAL_WIN"
    REPORTED_OPEN_ACTUAL_WIN = "REPORTED_OPEN_ACTUAL_WIN"
    REPORTED_OPEN_ACTUAL_LOSS = "REPORTED_OPEN_ACTUAL_LOSS"
    REPORTED_NO_REPORT_ACTUAL_WIN = "REPORTED_NO_REPORT_ACTUAL_WIN"
    REPORTED_NO_REPORT_ACTUAL_LOSS = "REPORTED_NO_REPORT_ACTUAL_LOSS"
    REPORTED_NO_REPORT_ACTUAL_NONE = "REPORTED_NO_REPORT_ACTUAL_NONE"
    AMBIGUOUS_BAR = "AMBIGUOUS_BAR"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DiscrepancyRow:
    signal_id: str
    group_id: str
    reported_state: OutcomeState
    actual_state: OutcomeState
    category: DiscrepancyCategory
    reported_pips: float | None
    actual_pips: float | None


def compute_discrepancies(
    *,
    db: Database,
    group_id: str | None = None,
) -> list[DiscrepancyRow]:
    sql = (
        "SELECT s.signal_id, s.group_id, "
        "       r.final_state AS reported_state, r.claimed_pips AS reported_pips, "
        "       a.final_state AS actual_state, a.first_touch_event AS first_touch_event, "
        "       a.realized_pips AS actual_pips "
        "FROM signals s "
        "LEFT JOIN reported_outcomes r ON r.signal_id = s.signal_id "
        "LEFT JOIN actual_outcomes a ON a.signal_id = s.signal_id"
    )
    params: list[object] = []
    if group_id is not None:
        sql += " WHERE s.group_id = ?"
        params.append(group_id)

    rows: list[DiscrepancyRow] = []
    for r in db.conn.execute(sql, params):
        reported_state = OutcomeState(r["reported_state"]) if r["reported_state"] else OutcomeState.NO_REPORT
        actual_state = OutcomeState(r["actual_state"]) if r["actual_state"] else OutcomeState.INSUFFICIENT_DATA
        category = _categorize(reported=reported_state, actual=actual_state)
        rows.append(
            DiscrepancyRow(
                signal_id=r["signal_id"],
                group_id=r["group_id"],
                reported_state=reported_state,
                actual_state=actual_state,
                category=category,
                reported_pips=r["reported_pips"],
                actual_pips=r["actual_pips"],
            )
        )
    return rows


def _categorize(
    *,
    reported: OutcomeState,
    actual: OutcomeState,
) -> DiscrepancyCategory:
    if actual == OutcomeState.INSUFFICIENT_DATA:
        return DiscrepancyCategory.INSUFFICIENT_DATA
    if actual == OutcomeState.AMBIGUOUS:
        return DiscrepancyCategory.AMBIGUOUS_BAR

    if reported == OutcomeState.WIN:
        if actual == OutcomeState.WIN:
            return DiscrepancyCategory.AGREES
        if actual == OutcomeState.LOSS:
            return DiscrepancyCategory.REPORTED_WIN_ACTUAL_LOSS
        return DiscrepancyCategory.REPORTED_WIN_ACTUAL_OPEN

    if reported == OutcomeState.LOSS:
        if actual == OutcomeState.LOSS:
            return DiscrepancyCategory.AGREES
        if actual == OutcomeState.WIN:
            return DiscrepancyCategory.REPORTED_LOSS_ACTUAL_WIN
        return DiscrepancyCategory.UNKNOWN

    if reported in (OutcomeState.OPEN, OutcomeState.OPEN_AT_EXPIRY, OutcomeState.BREAK_EVEN):
        if actual == OutcomeState.WIN:
            return DiscrepancyCategory.REPORTED_OPEN_ACTUAL_WIN
        if actual == OutcomeState.LOSS:
            return DiscrepancyCategory.REPORTED_OPEN_ACTUAL_LOSS
        return DiscrepancyCategory.AGREES

    if reported == OutcomeState.NO_REPORT:
        if actual == OutcomeState.WIN:
            return DiscrepancyCategory.REPORTED_NO_REPORT_ACTUAL_WIN
        if actual == OutcomeState.LOSS:
            return DiscrepancyCategory.REPORTED_NO_REPORT_ACTUAL_LOSS
        return DiscrepancyCategory.REPORTED_NO_REPORT_ACTUAL_NONE

    _ = (ActualOutcome, ReportedOutcome)  # silence unused-import in case of future refactor
    return DiscrepancyCategory.UNKNOWN
