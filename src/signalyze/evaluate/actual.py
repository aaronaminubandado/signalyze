"""Walk-forward actual-performance simulator.

Given a parsed `Signal` and the cached `MarketBar` time series, replay the
market bar-by-bar from the signal's timestamp and report whichever event is
*first touched*: an entry (if entry is a range that needs to be filled), a
stop-loss, one of the take-profits, or expiry (max_holding_hours).

Edge cases handled:
  - Same-bar ambiguity: when a single bar's high/low straddle both SL and a TP,
    the outcome is flagged as AMBIGUOUS (we cannot tell from OHLC alone which
    was touched first).
  - Missing entry price: if `entry` is None and the signal carries an entry
    range, we treat the trade as pending until the price enters that range
    inside the holding window.
  - Missing SL: when `default_sl_policy = NONE`, the trade can only exit via
    TP or expiry. When `default_sl_policy = FIXED_PIPS`, a synthetic SL is
    placed `default_sl_pips` away from the entry.
  - No market data: state is INSUFFICIENT_DATA.

Idempotent: re-running upserts existing rows in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from signalyze.config import Settings, get_settings
from signalyze.domain import (
    ActualOutcome,
    Direction,
    MarketBar,
    OutcomeState,
    QualityFlag,
    Signal,
    WinPolicy,
)
from signalyze.storage import Database
from signalyze.storage.repositories import (
    fetch_market_bars,
    fetch_signals,
    upsert_actual_outcome,
)
from signalyze.utils.logging import get_logger
from signalyze.utils.money import pips_for_xauusd
from signalyze.utils.time import format_utc, now_utc_iso, parse_utc

logger = get_logger("signalyze.evaluate.actual")


@dataclass
class SimulationStats:
    signals: int = 0
    outcomes_written: int = 0
    by_state: dict[str, int] = field(default_factory=dict)
    skipped_insufficient_data: int = 0

    def __post_init__(self) -> None:
        if not self.by_state:
            self.by_state = {s.value: 0 for s in OutcomeState}


@dataclass(frozen=True)
class SimulationConfig:
    win_policy: WinPolicy = WinPolicy.ANY_TP
    max_holding_hours: float = 168.0
    default_sl_policy: str = "NONE"  # "NONE" | "FIXED_PIPS"
    default_sl_pips: float = 50.0


def simulate_all(
    *,
    db: Database,
    settings: Settings | None = None,
    group_id: str | None = None,
    config: SimulationConfig | None = None,
) -> SimulationStats:
    settings = settings or get_settings()
    config = config or SimulationConfig(
        win_policy=WinPolicy(settings.evaluate.win_policy),
        max_holding_hours=settings.evaluate.max_holding_hours,
        default_sl_policy=settings.evaluate.default_sl_policy,
        default_sl_pips=settings.evaluate.default_sl_pips,
    )
    version = settings.evaluate.evaluator_version

    signals = fetch_signals(db.conn, group_id=group_id)
    stats = SimulationStats(signals=len(signals))

    for signal in signals:
        if signal.quality_flag == QualityFlag.MISSING_BOTH and signal.entry is None:
            outcome = _insufficient(signal, config=config, version=version)
        else:
            bars = _load_bars(db=db, signal=signal, hold_hours=config.max_holding_hours)
            if not bars:
                outcome = _insufficient(signal, config=config, version=version)
                stats.skipped_insufficient_data += 1
            else:
                outcome = _simulate_one(
                    signal=signal,
                    bars=bars,
                    config=config,
                    version=version,
                )

        with db.transaction() as conn:
            upsert_actual_outcome(conn, outcome)
        stats.outcomes_written += 1
        stats.by_state[outcome.final_state.value] += 1

    return stats


def _load_bars(*, db: Database, signal: Signal, hold_hours: float) -> list[MarketBar]:
    start = parse_utc(signal.timestamp_utc)
    end = start + timedelta(hours=hold_hours)
    return fetch_market_bars(
        db.conn,
        instrument=signal.instrument,
        interval="1min",
        start_utc=format_utc(start),
        end_utc=format_utc(end),
    )


def _simulate_one(
    *,
    signal: Signal,
    bars: list[MarketBar],
    config: SimulationConfig,
    version: str,
) -> ActualOutcome:
    entry_price = _effective_entry(signal, bars)
    if entry_price is None:
        return ActualOutcome(
            signal_id=signal.signal_id,
            final_state=OutcomeState.OPEN_AT_EXPIRY,
            first_touch_event="ENTRY_NOT_FILLED",
            win_policy=config.win_policy,
            max_holding_hours=config.max_holding_hours,
            default_sl_policy=config.default_sl_policy,
            computed_at=now_utc_iso(),
            computed_version=version,
        )

    sl_price = signal.stop_loss
    if sl_price is None and config.default_sl_policy == "FIXED_PIPS":
        sign = -1.0 if signal.direction == Direction.BUY else 1.0
        sl_price = entry_price + sign * (config.default_sl_pips * 0.1)

    touches: list[tuple[str, float]] = []  # (event_label, price)
    tp_levels = list(signal.take_profits)

    fill_dt = parse_utc(signal.timestamp_utc)
    bars_iterated = 0
    for bar in bars:
        bars_iterated += 1
        bar_dt = parse_utc(bar.timestamp_utc)
        if bar_dt < fill_dt:
            continue
        touches.clear()

        if sl_price is not None and _bar_touches(signal.direction, bar, sl_price, is_stop=True):
            touches.append(("SL", sl_price))

        for idx, tp in enumerate(tp_levels, start=1):
            if _bar_touches(signal.direction, bar, tp, is_stop=False):
                touches.append((f"TP{idx}", tp))

        if not touches:
            continue
        if len(touches) > 1 and any(t[0] == "SL" for t in touches):
            return _ambiguous(
                signal=signal,
                touches=touches,
                bar=bar,
                bars_iterated=bars_iterated,
                entry_price=entry_price,
                config=config,
                version=version,
            )

        event_label, event_price = touches[0]
        if event_label == "SL":
            return _loss(
                signal=signal,
                entry_price=entry_price,
                sl_price=event_price,
                bar=bar,
                bars_iterated=bars_iterated,
                config=config,
                version=version,
            )
        return _win(
            signal=signal,
            entry_price=entry_price,
            tp_event=event_label,
            tp_price=event_price,
            bar=bar,
            bars_iterated=bars_iterated,
            config=config,
            version=version,
        )

    return ActualOutcome(
        signal_id=signal.signal_id,
        final_state=OutcomeState.OPEN_AT_EXPIRY,
        first_touch_event="EXPIRY",
        first_touch_price=bars[-1].close,
        first_touch_at_utc=bars[-1].timestamp_utc,
        realized_pips=pips_for_xauusd(
            entry=entry_price,
            exit=bars[-1].close,
            direction=signal.direction.value,
        ),
        bars_to_outcome=bars_iterated,
        win_policy=config.win_policy,
        max_holding_hours=config.max_holding_hours,
        default_sl_policy=config.default_sl_policy,
        computed_at=now_utc_iso(),
        computed_version=version,
    )


def _effective_entry(signal: Signal, bars: list[MarketBar]) -> float | None:
    """Return the price the trade is filled at, walking bars if the signal uses a range."""
    if signal.entry is not None:
        return signal.entry
    if signal.entry_low is None or signal.entry_high is None:
        return None

    low = min(signal.entry_low, signal.entry_high)
    high = max(signal.entry_low, signal.entry_high)
    for bar in bars:
        if bar.low <= high and bar.high >= low:
            # First bar whose range intersects the entry range fills mid-band.
            return (low + high) / 2.0
    return None


def _bar_touches(
    direction: Direction,
    bar: MarketBar,
    level: float,
    *,
    is_stop: bool,
) -> bool:
    """Touched if the bar's high/low extreme reaches `level`.

    For longs (BUY): SL is below entry (use bar.low <= level), TP is above (bar.high >= level).
    For shorts (SELL): inverted.
    """
    if direction == Direction.BUY:
        return bar.low <= level if is_stop else bar.high >= level
    return bar.high >= level if is_stop else bar.low <= level


def _win(
    *,
    signal: Signal,
    entry_price: float,
    tp_event: str,
    tp_price: float,
    bar: MarketBar,
    bars_iterated: int,
    config: SimulationConfig,
    version: str,
) -> ActualOutcome:
    realized_pips = pips_for_xauusd(
        entry=entry_price,
        exit=tp_price,
        direction=signal.direction.value,
    )
    realized_rr = _rr_for(signal=signal, entry=entry_price, exit_price=tp_price)
    return ActualOutcome(
        signal_id=signal.signal_id,
        final_state=OutcomeState.WIN,
        first_touch_event=tp_event,
        first_touch_price=tp_price,
        first_touch_at_utc=bar.timestamp_utc,
        realized_rr=realized_rr,
        realized_pips=realized_pips,
        bars_to_outcome=bars_iterated,
        win_policy=config.win_policy,
        max_holding_hours=config.max_holding_hours,
        default_sl_policy=config.default_sl_policy,
        computed_at=now_utc_iso(),
        computed_version=version,
    )


def _loss(
    *,
    signal: Signal,
    entry_price: float,
    sl_price: float,
    bar: MarketBar,
    bars_iterated: int,
    config: SimulationConfig,
    version: str,
) -> ActualOutcome:
    realized_pips = pips_for_xauusd(
        entry=entry_price,
        exit=sl_price,
        direction=signal.direction.value,
    )
    return ActualOutcome(
        signal_id=signal.signal_id,
        final_state=OutcomeState.LOSS,
        first_touch_event="SL",
        first_touch_price=sl_price,
        first_touch_at_utc=bar.timestamp_utc,
        realized_rr=-1.0,
        realized_pips=realized_pips,
        bars_to_outcome=bars_iterated,
        win_policy=config.win_policy,
        max_holding_hours=config.max_holding_hours,
        default_sl_policy=config.default_sl_policy,
        computed_at=now_utc_iso(),
        computed_version=version,
    )


def _ambiguous(
    *,
    signal: Signal,
    touches: list[tuple[str, float]],
    bar: MarketBar,
    bars_iterated: int,
    entry_price: float,
    config: SimulationConfig,
    version: str,
) -> ActualOutcome:
    labels = "+".join(t[0] for t in touches)
    avg_price = sum(t[1] for t in touches) / len(touches)
    _ = entry_price  # kept for symmetry / future PnL conservatism
    return ActualOutcome(
        signal_id=signal.signal_id,
        final_state=OutcomeState.AMBIGUOUS,
        first_touch_event=f"AMBIGUOUS({labels})",
        first_touch_price=avg_price,
        first_touch_at_utc=bar.timestamp_utc,
        bars_to_outcome=bars_iterated,
        win_policy=config.win_policy,
        max_holding_hours=config.max_holding_hours,
        default_sl_policy=config.default_sl_policy,
        computed_at=now_utc_iso(),
        computed_version=version,
    )


def _insufficient(
    signal: Signal,
    *,
    config: SimulationConfig,
    version: str,
) -> ActualOutcome:
    return ActualOutcome(
        signal_id=signal.signal_id,
        final_state=OutcomeState.INSUFFICIENT_DATA,
        win_policy=config.win_policy,
        max_holding_hours=config.max_holding_hours,
        default_sl_policy=config.default_sl_policy,
        computed_at=now_utc_iso(),
        computed_version=version,
    )


def _rr_for(*, signal: Signal, entry: float, exit_price: float) -> float | None:
    if signal.stop_loss is None:
        return None
    risk = abs(entry - signal.stop_loss)
    if risk == 0:
        return None
    reward = abs(exit_price - entry)
    return round(reward / risk, 3)
