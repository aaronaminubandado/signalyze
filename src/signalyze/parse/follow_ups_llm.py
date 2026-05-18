"""LLM fallback for follow-up event parsing."""

from __future__ import annotations

from signalyze.domain import FollowUpEventType
from signalyze.llm import LLMClient, LLMUnavailableError
from signalyze.parse.follow_ups_rules import ParsedFollowUpPayload

_PROMPT_VERSION = "parse-followup-v1"

_SYSTEM_PROMPT = """You classify a single follow-up message from a trading-signal group.
Return JSON conforming to the provided schema.

Possible event types (pick exactly one):
- TP_HIT: a take-profit target was hit. Include the TP index if mentioned.
- SL_HIT: stop-loss was hit / stopped out.
- BE_MOVED: stop-loss moved to break-even / entry.
- SL_MOVED: stop-loss moved to a specific numeric value. Capture new_stop_loss.
- MANUAL_CLOSE: trader manually closed the position.
- CANCEL: signal was cancelled / voided.
- UPDATE: a generic progress update (running pips, hold, etc.).
- AMBIGUOUS: the message is unclear.

If you can capture a claimed price or claimed pips count, include them; otherwise
return null. Do NOT invent values not in the message text.
"""

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "event_type",
        "tp_index",
        "claimed_price",
        "claimed_pips",
        "new_stop_loss",
        "confidence",
    ],
    "properties": {
        "event_type": {
            "type": "string",
            "enum": [
                "TP_HIT",
                "SL_HIT",
                "BE_MOVED",
                "SL_MOVED",
                "MANUAL_CLOSE",
                "CANCEL",
                "UPDATE",
                "AMBIGUOUS",
            ],
        },
        "tp_index": {"type": ["integer", "null"]},
        "claimed_price": {"type": ["number", "null"]},
        "claimed_pips": {"type": ["number", "null"]},
        "new_stop_loss": {"type": ["number", "null"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}


def llm_parse_follow_up(
    text: str, client: LLMClient
) -> tuple[ParsedFollowUpPayload | None, float, list[str]]:
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

    event_raw = str(response.get("event_type", "")).upper()
    try:
        event_type = FollowUpEventType(event_raw)
    except ValueError:
        return None, 0.0, ["llm:bad_event_type"]

    payload = ParsedFollowUpPayload(
        event_type=event_type,
        tp_index=_opt_int(response.get("tp_index")),
        claimed_price=_opt_float(response.get("claimed_price")),
        claimed_pips=_opt_float(response.get("claimed_pips")),
        new_stop_loss=_opt_float(response.get("new_stop_loss")),
    )
    confidence = float(response.get("confidence", 0.6))
    return payload, max(0.0, min(1.0, confidence)), ["llm:parsed"]


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
