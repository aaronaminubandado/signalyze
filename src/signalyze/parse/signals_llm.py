"""LLM fallback for signal parsing. Only invoked when rule confidence is low."""

from __future__ import annotations

from signalyze.domain import Direction, QualityFlag
from signalyze.llm import LLMClient, LLMUnavailableError
from signalyze.parse.signals_rules import ParsedSignalPayload

_PROMPT_VERSION = "parse-signal-v1"

_SYSTEM_PROMPT = """You extract a single trading signal from a Telegram message.
Return JSON conforming to the provided schema. If the message is NOT a trade setup
(it is a follow-up, a promo, or chatter), return null fields.

Rules:
- direction must be BUY or SELL.
- instrument is always XAUUSD for v1.
- entry is the price the trader will enter at. If the message gives a range,
  return entry_low and entry_high but leave entry null.
- stop_loss and take_profits must be present in the message text. Do NOT invent
  values. Each take profit must be greater than entry for BUY and less than
  entry for SELL.
- All prices must be plausible XAUUSD prices (between 1500 and 5000).
"""

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "is_signal",
        "direction",
        "instrument",
        "entry",
        "entry_low",
        "entry_high",
        "stop_loss",
        "take_profits",
        "confidence",
    ],
    "properties": {
        "is_signal": {"type": "boolean"},
        "direction": {"type": ["string", "null"], "enum": ["BUY", "SELL", None]},
        "instrument": {"type": ["string", "null"]},
        "entry": {"type": ["number", "null"]},
        "entry_low": {"type": ["number", "null"]},
        "entry_high": {"type": ["number", "null"]},
        "stop_loss": {"type": ["number", "null"]},
        "take_profits": {"type": "array", "items": {"type": "number"}},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}


def llm_parse_signal(
    text: str, client: LLMClient
) -> tuple[ParsedSignalPayload | None, float, list[str]]:
    """Run the LLM fallback. Returns (payload | None, confidence, reasons)."""
    if not client.is_available:
        return None, 0.0, ["llm_unavailable"]

    try:
        response = client.structured_call(
            prompt_version=_PROMPT_VERSION,
            system=_SYSTEM_PROMPT,
            user=text,
            schema=_SCHEMA,
        )
    except LLMUnavailableError:
        return None, 0.0, ["llm_unavailable"]

    if not response.get("is_signal"):
        return None, float(response.get("confidence", 0.0)), ["llm:not_signal"]

    direction_raw = response.get("direction")
    if direction_raw not in {"BUY", "SELL"}:
        return None, float(response.get("confidence", 0.0)), ["llm:bad_direction"]

    take_profits_raw = response.get("take_profits") or []
    take_profits = [float(x) for x in take_profits_raw if isinstance(x, (int, float))]

    entry = _opt_float(response.get("entry"))
    entry_low = _opt_float(response.get("entry_low"))
    entry_high = _opt_float(response.get("entry_high"))
    stop_loss = _opt_float(response.get("stop_loss"))

    if entry is None and (entry_low is None or entry_high is None):
        return None, float(response.get("confidence", 0.0)), ["llm:no_entry"]

    if entry_low is not None and entry_high is not None and entry_low > entry_high:
        entry_low, entry_high = entry_high, entry_low

    quality = _quality(
        entry_low=entry_low, entry_high=entry_high, sl=stop_loss, tps=take_profits
    )

    payload = ParsedSignalPayload(
        direction=Direction(direction_raw),
        instrument="XAUUSD",
        entry=entry,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        take_profits=take_profits,
        quality_flag=quality,
    )
    confidence = float(response.get("confidence", 0.6))
    return payload, max(0.0, min(1.0, confidence)), ["llm:parsed"]


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _quality(
    *,
    entry_low: float | None,
    entry_high: float | None,
    sl: float | None,
    tps: list[float],
) -> QualityFlag:
    if entry_low is not None and entry_high is not None and sl is None and not tps:
        return QualityFlag.ENTRY_RANGE
    if sl is None and not tps:
        return QualityFlag.MISSING_BOTH
    if sl is None:
        return QualityFlag.MISSING_SL
    if not tps:
        return QualityFlag.MISSING_TP
    return QualityFlag.COMPLETE
