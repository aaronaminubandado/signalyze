"""Pip and price math, scoped to v1's XAUUSD focus."""

from __future__ import annotations

XAUUSD_PIP_SIZE = 0.1


def xauusd_pips(price_delta: float) -> float:
    """Convert an XAUUSD price delta (e.g., 4710 - 4700 = 10.0) to pips."""
    return price_delta / XAUUSD_PIP_SIZE


def xauusd_price_diff(pips: float) -> float:
    """Convert XAUUSD pips back to a price delta."""
    return pips * XAUUSD_PIP_SIZE


def pips_for_xauusd(*, entry: float, exit: float, direction: str) -> float:
    """Signed P&L in pips for an XAUUSD trade.

    Positive for a winning trade regardless of direction, negative for a loser.
    `direction` accepts "BUY" / "SELL" (case-insensitive).
    """
    delta = exit - entry
    if direction.upper() == "SELL":
        delta = -delta
    return round(xauusd_pips(delta), 2)
