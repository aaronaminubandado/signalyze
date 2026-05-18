# ADR 005 — Walk-forward simulator with first-touch + same-bar ambiguity flagging

- **Status:** Accepted
- **Date:** 2026-04

## Decision

Actual performance is computed by a **walk-forward simulator** that consumes
cached 1-minute OHLCV bars and produces one `ActualOutcome` per signal:

- Bars are scanned in chronological order starting at the signal's timestamp.
- For each bar, check whether the bar's `[low, high]` interval touches the
  stop-loss or any take-profit level.
- The first event wins, except when both SL and a TP appear in the same bar:
  the outcome is flagged `AMBIGUOUS` and surfaced separately.
- If no level is hit within `evaluate.max_holding_hours` (default 168), the
  outcome is `OPEN_AT_EXPIRY` and recorded with the closing price of the last
  inspected bar.
- The `win_policy` (`ANY_TP` by default), the holding cap, and a
  `default_sl_policy` are recorded with every outcome so reruns under
  different policies are explicit.

## Why

- **First-touch is the only honest model on 1-minute bars.** A trader watching
  the chart would have exited the moment the price crossed the SL line, not
  at the bar's close. Pretending otherwise inflates the actual win rate.
- **Same-bar ambiguity is real and common on XAUUSD.** During news prints,
  a single minute can carry both an SL hit and a TP hit. Hiding these as a
  win or a loss biases results; flagging them makes the bias visible.
- **Configurable policies = honest comparisons.** Some signal groups never
  specify a stop-loss. Comparing "win rates with no SL" vs "win rates with a
  default 50-pip SL" exposes the survivor bias in the reported numbers.
- **Walk-forward, not vectorised lookup.** A vectorised "did the price ever
  cross 4710?" check is fast but cannot determine *which* level was touched
  first. Walking the bars is O(n) but produces the right answer.

## Alternatives considered

1. **Pandas-style vectorised lookup.** Rejected: cannot encode first-touch
   semantics or same-bar ambiguity without per-row Python anyway.
2. **Tick-by-tick simulation.** Rejected for v1: tick data is rate-limited and
   expensive, and 1-minute bars are sufficient for the analytical goal of
   *comparing reported claims with reality*.
3. **Higher-resolution bars only on disputed signals.** Possible future work,
   tracked but not implemented.

## Consequences

- The simulator depends on having sufficient market-data coverage; the
  `signalyze market fetch` command is idempotent and only fills gaps.
- `OutcomeState.AMBIGUOUS` is a first-class state, surfaced in the dashboard.
- Changing the `win_policy` or `max_holding_hours` produces a new
  `computed_version` in `actual_outcomes`, so historical comparisons remain
  reproducible.
