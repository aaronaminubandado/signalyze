"""LLM tiebreaker for contested link candidates."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from signalyze.domain import FollowUpEvent
from signalyze.llm import LLMClient, LLMUnavailableError

if TYPE_CHECKING:
    from signalyze.link.linker import LinkCandidate

_PROMPT_VERSION = "link-tiebreak-v1"

_SYSTEM_PROMPT = """You disambiguate which prior trading signal a follow-up message refers to.
You will receive the follow-up text and up to 5 candidate signals (each with id, timestamp,
direction, entry, stop_loss, take_profits). Return the index (0-based) of the most plausible
match, or -1 if none match.
"""

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pick_index", "confidence", "reason"],
    "properties": {
        "pick_index": {"type": "integer", "minimum": -1},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
    },
}


def llm_tiebreak(
    *,
    event: FollowUpEvent,
    candidates: list[LinkCandidate],
    client: LLMClient,
) -> LinkCandidate | None:
    if not client.is_available or not candidates:
        return None

    body = json.dumps(
        {
            "follow_up": {
                "text": "",  # the text itself is contained inside event reasons / parse output
                "event_type": event.event_type.value,
                "tp_index": event.tp_index,
                "claimed_price": event.claimed_price,
                "claimed_pips": event.claimed_pips,
                "timestamp_utc": event.timestamp_utc,
            },
            "candidates": [
                {
                    "index": i,
                    "signal_id": c.signal.signal_id,
                    "timestamp_utc": c.signal.timestamp_utc,
                    "direction": c.signal.direction.value,
                    "entry": c.signal.entry,
                    "entry_low": c.signal.entry_low,
                    "entry_high": c.signal.entry_high,
                    "stop_loss": c.signal.stop_loss,
                    "take_profits": c.signal.take_profits,
                    "rule_score": c.score,
                }
                for i, c in enumerate(candidates)
            ],
        }
    )

    try:
        response = client.structured_call(
            prompt_version=_PROMPT_VERSION,
            system=_SYSTEM_PROMPT,
            user=body,
            schema=_SCHEMA,
        )
    except LLMUnavailableError:
        return None

    try:
        pick = int(response.get("pick_index", -1))
    except (TypeError, ValueError):
        return None

    if pick < 0 or pick >= len(candidates):
        return None
    return candidates[pick]
