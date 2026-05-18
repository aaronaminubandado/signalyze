"""LLM-based fallback classifier. Called only when the rule classifier returns UNCERTAIN."""

from __future__ import annotations

from signalyze.classify.rules import ClassificationResult
from signalyze.domain import Message, MessageClass
from signalyze.llm import LLMClient, LLMUnavailableError

_PROMPT_VERSION = "classify-v1"

_SYSTEM_PROMPT = """You classify a single Telegram message from a trading-signal group.
Return ONE of: SIGNAL, FOLLOW_UP, NOISE.

Definitions:
- SIGNAL: a new trade setup with a direction (BUY/SELL), an instrument (XAUUSD/GOLD), and at least one of an entry price, stop loss, or take profit.
- FOLLOW_UP: an update about a previously posted signal: TP hit, SL hit, "move SL", break-even, manual close, cancel.
- NOISE: anything else (promos, VIP pitches, payment instructions, generic chatter, empty / emoji-only messages).

Be strict: if a message contains BOTH a complete signal structure AND outcome language, prefer SIGNAL only if there is clearly a new setup being announced.
"""

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["class", "confidence", "reason"],
    "properties": {
        "class": {"type": "string", "enum": ["SIGNAL", "FOLLOW_UP", "NOISE"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
    },
}


def llm_classify(message: Message, client: LLMClient) -> ClassificationResult | None:
    """Run the LLM fallback. Returns `None` if the LLM is unavailable."""
    if not client.is_available:
        return None

    user_prompt = (
        f"Message text:\n---\n{message.text}\n---\n"
        f"reply_to_msg_id: {message.reply_to_msg_id}\n"
    )

    try:
        response = client.structured_call(
            prompt_version=_PROMPT_VERSION,
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            schema=_SCHEMA,
        )
    except LLMUnavailableError:
        return None

    cls_raw = str(response.get("class", "")).upper()
    if cls_raw not in {"SIGNAL", "FOLLOW_UP", "NOISE"}:
        return None
    confidence = float(response.get("confidence", 0.5))
    reason = str(response.get("reason", ""))

    return ClassificationResult(
        message_class=MessageClass(cls_raw),
        confidence=max(0.0, min(1.0, confidence)),
        reasons=[f"llm:{reason}"],
    )
