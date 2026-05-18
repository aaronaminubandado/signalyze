"""Deterministic follow-up event parser.

Maps a Telegram follow-up message into one of:
    TP_HIT, SL_HIT, BE_MOVED, SL_MOVED, MANUAL_CLOSE, UPDATE, CANCEL, AMBIGUOUS

Always returns either a `ParsedFollowUpPayload` (when event type is identifiable)
or `None` when the message is too vague (caller may escalate to LLM).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from signalyze.config import Settings, get_settings
from signalyze.domain import FollowUpEventType

# Token patterns.
_TP_HIT_RE = re.compile(
    r"\bTP\s*(\d+)\b(?:[^A-Za-z0-9\n]{0,12})"
    r"\b(?:HIT|HITS|REACHED|ACHIEVED|TAPPED?|BOOK(?:ED)?|SUCCESSFUL|SUCCESS|DONE|COMPLETE[D]?|SECURED)\b",
    re.IGNORECASE,
)
_TP_HIT_LOOSE_RE = re.compile(
    r"\b(?:HIT|REACHED|ACHIEVED|TAPPED?|BOOK(?:ED)?|SUCCESSFUL|SECURED)\b\s+TP\s*(\d+)\b",
    re.IGNORECASE,
)
_TARGET_COMPLETE_RE = re.compile(
    r"\bTARGET\s*[¹²³⁴⁵\d]+\s*(?:COMPLETE[D]?|HIT|DONE|REACHED)\b",
    re.IGNORECASE,
)
_ALL_TPS_HIT_RE = re.compile(r"\bALL\s+TP[S]?\s+(?:HIT|HITS|REACHED)\b", re.IGNORECASE)

_SL_HIT_RE = re.compile(r"\bSL\s+(?:HIT|HITS|TAKEN|STOPPED\s+OUT|STRUCK)\b", re.IGNORECASE)
_STOPPED_OUT_RE = re.compile(r"\bSTOPPED\s+OUT\b|\bSTOP\s+(?:LOSS\s+)?HIT\b", re.IGNORECASE)

_BE_RE = re.compile(
    r"\b(?:MOVE\s+SL\s+(?:TO\s+)?(?:BE|BREAK\s*EVEN|ENTRY)|"
    r"BREAK\s*EVEN\s+(?:SECURED|HIT)|"
    r"BE\s+SECURED|"
    r"SL\s+TO\s+(?:BE|ENTRY|BREAK\s*EVEN))\b",
    re.IGNORECASE,
)
_SL_MOVED_RE = re.compile(
    r"\bMOVE\s+SL\s+(?:TO\s+)?(\d+(?:\.\d+)?)\b|"
    r"\bSL\s+(?:NOW\s+)?(?:AT\s+|MOVED\s+TO\s+)(\d+(?:\.\d+)?)\b|"
    r"\bUPDATE\s+SL\s+(?:TO\s+)?(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"\b(CANCEL(?:LED|LING)?|VOID(?:ED|ING)?|IGNORE\s+(?:THE\s+)?SIGNAL)\b",
    re.IGNORECASE,
)
_MANUAL_CLOSE_RE = re.compile(
    r"\b(CLOSE\s+ALL|CLOSE\s+(?:THE\s+)?TRADE|CLOSED\s+AT|CLOSING\s+(?:THE\s+)?TRADE|"
    r"EXIT\s+(?:THE\s+)?TRADE|FULL\s+CLOSE|CLOSE\s+NOW|CLOSE\s+(?:THE\s+)?POSITION)\b",
    re.IGNORECASE,
)
_PIPS_RE = re.compile(r"([+\-]?)\s*(\d{1,4})\s*\+?\s*PIPS?\b", re.IGNORECASE)
_AT_PRICE_RE = re.compile(r"\bAT\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedFollowUpPayload:
    """Pure data carrier; mapped into `FollowUpEvent` by the runner."""

    event_type: FollowUpEventType
    tp_index: int | None
    claimed_price: float | None
    claimed_pips: float | None
    new_stop_loss: float | None


@dataclass(frozen=True)
class FollowUpParseResult:
    payload: ParsedFollowUpPayload | None
    confidence: float
    reasons: list[str]


class FollowUpRuleParser:
    """Deterministic follow-up parser."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.version = self.settings.parse.parser_version
        self.xau_min = self.settings.instrument.xauusd_min_price
        self.xau_max = self.settings.instrument.xauusd_max_price

    def parse_text(self, text: str) -> FollowUpParseResult:
        if not text or not text.strip():
            return FollowUpParseResult(None, 0.0, ["empty_text"])

        reasons: list[str] = []
        upper = text.upper()

        # CANCEL has highest priority — it overrides outcome events.
        if _CANCEL_RE.search(upper):
            reasons.append("cancel_keyword")
            return _result(FollowUpEventType.CANCEL, 0.9, reasons)

        # SL_HIT and TP_HIT are mutually exclusive within one message; flag ambiguity if both.
        tp_index = self._extract_tp_hit_index(upper)
        sl_hit = bool(_SL_HIT_RE.search(upper) or _STOPPED_OUT_RE.search(upper))
        all_tps = bool(_ALL_TPS_HIT_RE.search(upper))

        if tp_index is not None and sl_hit:
            reasons.append("tp_and_sl_in_same_message")
            return _result(
                FollowUpEventType.AMBIGUOUS,
                0.5,
                reasons,
                tp_index=tp_index,
            )

        if sl_hit:
            reasons.append("sl_hit")
            return _result(FollowUpEventType.SL_HIT, 0.9, reasons, claimed_pips=self._signed_pips(text))

        if tp_index is not None or all_tps:
            reasons.append(f"tp_hit_index={tp_index}" if tp_index else "all_tps")
            return _result(
                FollowUpEventType.TP_HIT,
                0.9,
                reasons,
                tp_index=tp_index,
                claimed_price=self._claimed_price(text),
                claimed_pips=self._signed_pips(text),
            )

        # BE / SL movements.
        if _BE_RE.search(upper):
            reasons.append("be_keyword")
            return _result(FollowUpEventType.BE_MOVED, 0.85, reasons)

        sl_moved = self._extract_sl_moved(upper)
        if sl_moved is not None:
            reasons.append(f"sl_moved_to={sl_moved}")
            return _result(FollowUpEventType.SL_MOVED, 0.85, reasons, new_stop_loss=sl_moved)

        if _MANUAL_CLOSE_RE.search(upper):
            reasons.append("manual_close")
            return _result(
                FollowUpEventType.MANUAL_CLOSE,
                0.8,
                reasons,
                claimed_price=self._claimed_price(text),
                claimed_pips=self._signed_pips(text),
            )

        # Bare pip statements: "+30 pips" → UPDATE (running profit).
        if _PIPS_RE.search(text):
            reasons.append("pips_only")
            return _result(
                FollowUpEventType.UPDATE,
                0.6,
                reasons,
                claimed_pips=self._signed_pips(text),
            )

        return FollowUpParseResult(None, 0.0, ["no_event"])

    def _extract_tp_hit_index(self, text: str) -> int | None:
        for pattern in (_TP_HIT_RE, _TP_HIT_LOOSE_RE):
            match = pattern.search(text)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue
        return None

    def _extract_sl_moved(self, text: str) -> float | None:
        match = _SL_MOVED_RE.search(text)
        if not match:
            return None
        for group_value in match.groups():
            if group_value is None:
                continue
            try:
                price = float(group_value)
            except ValueError:
                continue
            if self.xau_min <= price <= self.xau_max:
                return price
        return None

    def _signed_pips(self, text: str) -> float | None:
        match = _PIPS_RE.search(text)
        if not match:
            return None
        try:
            value = float(match.group(2))
        except ValueError:
            return None
        sign = -1.0 if match.group(1) == "-" else 1.0
        return sign * value

    def _claimed_price(self, text: str) -> float | None:
        match = _AT_PRICE_RE.search(text)
        if not match:
            return None
        try:
            price = float(match.group(1))
        except ValueError:
            return None
        if self.xau_min <= price <= self.xau_max:
            return price
        return None


def _result(
    event_type: FollowUpEventType,
    confidence: float,
    reasons: list[str],
    *,
    tp_index: int | None = None,
    claimed_price: float | None = None,
    claimed_pips: float | None = None,
    new_stop_loss: float | None = None,
) -> FollowUpParseResult:
    payload = ParsedFollowUpPayload(
        event_type=event_type,
        tp_index=tp_index,
        claimed_price=claimed_price,
        claimed_pips=claimed_pips,
        new_stop_loss=new_stop_loss,
    )
    return FollowUpParseResult(payload=payload, confidence=confidence, reasons=reasons)
