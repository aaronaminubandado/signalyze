"""Deterministic signal extractor.

Returns a `SignalParseResult` carrying the parsed payload (if any), a confidence
score in [0, 1], and a list of human-readable reasons. The caller decides whether
to escalate to an LLM fallback when confidence is too low.

Plausibility rules:
- Direction in {BUY, SELL} (LONG/SHORT normalized).
- Instrument is XAUUSD (or GOLD -> XAUUSD).
- All numeric fields must fall inside `[xau_min, xau_max]` to be accepted.
- Entry is either a single price or a low/high range.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from signalyze.config import Settings, get_settings
from signalyze.domain import Direction, QualityFlag

_DIRECTION_RE = re.compile(
    r"\b(BUY|SELL|LONG|SHORT)(?:\s+(?:NOW|LIMIT|STOP|ZONE))?\b",
    re.IGNORECASE,
)
_INSTRUMENT_RE = re.compile(r"(?:#\s*)?\b(XAUUSD|XAU/USD|GOLD)\b", re.IGNORECASE)
_PRICE_RE = re.compile(r"\d+(?:\.\d+)?")

_SL_LINE_RE = re.compile(
    r"\b(SL|S/L|STOP\s*LOSS|STOPLOSS)\b[\s:\-.@]*?(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_TP_LINE_RE = re.compile(
    r"\bTP\s*\d?\b[\s:\-.@]*?(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_TAKE_PROFIT_LINE_RE = re.compile(
    r"\bTAKE\s*PROFIT\s*\d?\b[\s:\-.@]*?(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_ENTRY_RANGE_RE = re.compile(
    r"\b(\d{3,5}(?:\.\d+)?)\s*(?:-|/|to)\s*(\d{3,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_ENTRY_KW_RE = re.compile(
    r"\b(?:ENTRY(?:\s*PRICE)?|PRICE|AT)\b\s*[:\-]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedSignalPayload:
    """Pure data carrier; mapped into `Signal` by the runner."""

    direction: Direction
    instrument: str
    entry: float | None
    entry_low: float | None
    entry_high: float | None
    stop_loss: float | None
    take_profits: list[float]
    quality_flag: QualityFlag


@dataclass(frozen=True)
class SignalParseResult:
    """Output of `SignalRuleParser.parse_text`."""

    payload: ParsedSignalPayload | None
    confidence: float
    reasons: list[str]


class SignalRuleParser:
    """Deterministic regex/heuristic parser for XAUUSD signals."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.version = self.settings.parse.parser_version
        self.xau_min = self.settings.instrument.xauusd_min_price
        self.xau_max = self.settings.instrument.xauusd_max_price

    def parse_text(self, text: str) -> SignalParseResult:
        reasons: list[str] = []
        normalized = _normalize(text)
        if not normalized:
            return SignalParseResult(None, 0.0, ["empty_text"])

        direction = self._extract_direction(normalized)
        if direction is None:
            return SignalParseResult(None, 0.0, ["no_direction"])
        reasons.append(f"direction={direction.value}")

        instrument = self._extract_instrument(normalized)
        if instrument is None:
            return SignalParseResult(None, 0.0, ["no_instrument"])
        reasons.append(f"instrument={instrument}")

        sl = self._extract_sl(normalized)
        tps = self._extract_take_profits(normalized)
        entry, entry_low, entry_high, entry_reasons = self._extract_entry(
            normalized, direction=direction
        )
        reasons.extend(entry_reasons)

        if entry is None and (entry_low is None or entry_high is None):
            return SignalParseResult(None, 0.0, [*reasons, "no_entry"])

        if not self._validate_prices(entry=entry, entry_low=entry_low, entry_high=entry_high, sl=sl, tps=tps):
            return SignalParseResult(None, 0.0, [*reasons, "implausible_prices"])

        quality = _build_quality_flag(sl=sl, tps=tps, entry_low=entry_low, entry_high=entry_high)
        confidence = self._compute_confidence(direction=direction, sl=sl, tps=tps, entry=entry)

        payload = ParsedSignalPayload(
            direction=direction,
            instrument=instrument,
            entry=entry,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=sl,
            take_profits=tps,
            quality_flag=quality,
        )
        return SignalParseResult(payload=payload, confidence=confidence, reasons=reasons)

    def _extract_direction(self, text: str) -> Direction | None:
        match = _DIRECTION_RE.search(text)
        if not match:
            return None
        token = match.group(1).upper()
        if token in {"BUY", "LONG"}:
            return Direction.BUY
        if token in {"SELL", "SHORT"}:
            return Direction.SELL
        return None

    def _extract_instrument(self, text: str) -> str | None:
        match = _INSTRUMENT_RE.search(text)
        if not match:
            return None
        token = match.group(1).upper().replace("/", "")
        return "XAUUSD" if token in {"GOLD", "XAUUSD"} else token

    def _extract_sl(self, text: str) -> float | None:
        match = _SL_LINE_RE.search(text)
        if not match:
            return None
        try:
            return float(match.group(2))
        except ValueError:
            return None

    def _extract_take_profits(self, text: str) -> list[float]:
        seen: set[str] = set()
        out: list[float] = []
        for pattern in (_TP_LINE_RE, _TAKE_PROFIT_LINE_RE):
            for match in pattern.finditer(text):
                try:
                    value = float(match.group(1))
                except ValueError:
                    continue
                token = f"{value:.4f}"
                if token in seen:
                    continue
                seen.add(token)
                out.append(value)
        return out

    def _extract_entry(
        self,
        text: str,
        *,
        direction: Direction,
    ) -> tuple[float | None, float | None, float | None, list[str]]:
        """Return (entry, entry_low, entry_high, reasons)."""
        reasons: list[str] = []

        # 1. Explicit entry range (e.g. `XAUUSD Sell 4675/4678`).
        range_entry = self._extract_entry_range(text)
        if range_entry is not None:
            entry_low, entry_high = range_entry
            reasons.append("entry_range")
            return None, entry_low, entry_high, reasons

        # 2. Direction line. Skip lines that are clearly outcome announcements.
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or direction.value not in line:
                continue
            if _is_outcome_line(line):
                continue
            for match in _PRICE_RE.finditer(line):
                try:
                    value = float(match.group(0))
                except ValueError:
                    continue
                if self.xau_min <= value <= self.xau_max:
                    reasons.append("entry_from_direction_line")
                    return value, None, None, reasons

        # 3. ENTRY/PRICE/AT keyword anywhere in text.
        kw_match = _ENTRY_KW_RE.search(text)
        if kw_match is not None:
            kw_value: float | None
            try:
                kw_value = float(kw_match.group(1))
            except ValueError:
                kw_value = None
            if kw_value is not None and self.xau_min <= kw_value <= self.xau_max:
                reasons.append("entry_from_keyword")
                return kw_value, None, None, reasons

        return None, None, None, [*reasons, "no_entry_candidate"]

    def _extract_entry_range(self, text: str) -> tuple[float, float] | None:
        for match in _ENTRY_RANGE_RE.finditer(text):
            try:
                a = float(match.group(1))
                b = float(match.group(2))
            except ValueError:
                continue
            if not (self.xau_min <= a <= self.xau_max and self.xau_min <= b <= self.xau_max):
                continue
            low, high = sorted((a, b))
            if high - low > 50.0:  # XAUUSD entry ranges are tight; otherwise it's likely TP1/SL.
                continue
            return low, high
        return None

    def _validate_prices(
        self,
        *,
        entry: float | None,
        entry_low: float | None,
        entry_high: float | None,
        sl: float | None,
        tps: list[float],
    ) -> bool:
        prices: list[float] = []
        prices.extend([p for p in (entry, entry_low, entry_high, sl) if p is not None])
        prices.extend(tps)
        if not prices:
            return False
        return all(self.xau_min <= p <= self.xau_max for p in prices)

    def _compute_confidence(
        self,
        *,
        direction: Direction,
        sl: float | None,
        tps: list[float],
        entry: float | None,
    ) -> float:
        """High confidence requires direction + entry + (sl AND tps); penalise missing pieces."""
        score = 0.4  # baseline for matching direction + instrument
        if entry is not None:
            score += 0.2
        if sl is not None:
            score += 0.2
        if tps:
            score += 0.15 + min(0.05, 0.01 * (len(tps) - 1))
        return round(min(1.0, score), 3)


def _normalize(text: str) -> str:
    cleaned = text.replace("\r", "\n")
    cleaned = cleaned.replace("**", " ")
    cleaned = re.sub(r"[*_`]", " ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    return cleaned.upper().strip()


def _is_outcome_line(line: str) -> bool:
    return bool(
        re.search(
            r"\b(HIT|HITS|TAPPED?|RAN|RUNNING|RUNS|SECURED?|"
            r"BOOK(?:ED)?|CLOSE(?:D)?|CLOSING|STOPPED|"
            r"REACHED|ACHIEVED|TARGET\s+HIT)\b",
            line,
            re.IGNORECASE,
        )
    )


def _build_quality_flag(
    *,
    sl: float | None,
    tps: list[float],
    entry_low: float | None,
    entry_high: float | None,
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
