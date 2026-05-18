"""Rule-based classifier for messages.

Design:
- Score each message against three feature buckets: SIGNAL, FOLLOW_UP, NOISE.
- Use lexical evidence + numeric plausibility + structural hints.
- Return the highest-scoring class with a `confidence` derived from the margin.
- If no class clears the configured threshold, return `UNCERTAIN` so the
  caller can decide whether to escalate to the LLM fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from signalyze.config import Settings, get_settings
from signalyze.domain import Message, MessageClass


@dataclass(frozen=True)
class ClassificationResult:
    """Output of `RuleClassifier.classify`."""

    message_class: MessageClass
    confidence: float
    reasons: list[str]


# Direction-and-instrument anchors. Required (in some form) for a SIGNAL.
_INSTRUMENT_RE = re.compile(r"\b(?:#\s*)?(XAUUSD|XAU/USD|GOLD)\b", re.IGNORECASE)
_DIRECTION_RE = re.compile(
    r"\b(BUY|SELL|LONG|SHORT)(?:\s+(?:NOW|LIMIT|STOP|ZONE))?\b",
    re.IGNORECASE,
)

# Signal-only structural tokens.
_SL_TOKEN_RE = re.compile(r"\b(SL|S/L|STOP\s*LOSS|STOPLOSS)\b\s*[:\-@]?\s*\d", re.IGNORECASE)
_TP_TOKEN_RE = re.compile(r"\bTP\s*\d?\b\s*[:\-@]?\s*\d", re.IGNORECASE)
_TAKE_PROFIT_RE = re.compile(r"\bTAKE\s*PROFIT\s*\d?\b\s*[:\-@]?\s*\d", re.IGNORECASE)
_ENTRY_TOKEN_RE = re.compile(r"\b(ENTRY|ENTRY\s*ZONE|PRICE|@)\b", re.IGNORECASE)

# Follow-up outcome verbs.
_OUTCOME_VERB_RE = re.compile(
    r"\b("
    r"HIT|HITS|TAPPED?|RAN|RUNNING|RUNS|SECURED?|BOOK(?:ED)?|"
    r"CLOSE(?:D)?|CLOSING|STOPPED|TRAILING|"
    r"REACHED|ACHIEVED|TARGET\s+HIT"
    r")\b",
    re.IGNORECASE,
)
_PIPS_PROFIT_RE = re.compile(r"[+\-]?\s*\d{1,4}\s*PIPS?\b", re.IGNORECASE)
_BE_RE = re.compile(r"\b(BE|BREAK\s*EVEN|BREAKEVEN|MOVE\s*SL)\b", re.IGNORECASE)
_CANCEL_RE = re.compile(r"\b(CANCEL(?:LED|LING)?|VOID|IGNORE\s+SIGNAL)\b", re.IGNORECASE)

# Noise / promo lexicon. Use letter/digit boundaries so tokens still match inside
# strings like `VIP_SIGNALS` or `#VIP_signals` where `_` is a word character.
_PROMO_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"VIP|PAYMENT|WALLET|TRC20|JOIN\s+HERE|"
    r"WHATSAPP|MONIE\s*POINT|BONUS|DEPOSIT|"
    r"SUBSCRIBE|REGISTER|AFFILIATE|VIPCODE|"
    r"REFERRAL|PROMO|"
    r"NEW\s+USER\s+DEAL"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PRICE_RE = re.compile(r"\b\d{3,5}(?:\.\d+)?\b")

# Single-line classifier for "GOLD" appearing in mixed promo text.
_GOLD_PROMO_HINT_RE = re.compile(r"\b(XAUT|GOLD\s+TOKEN|TETHER\s*GOLD)\b", re.IGNORECASE)


class RuleClassifier:
    """Deterministic, rule-based message classifier.

    Stateless across calls; constructed once per pipeline run.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.threshold = self.settings.classify.rule_confidence_threshold
        self.version = self.settings.classify.classifier_version
        self.xau_min = self.settings.instrument.xauusd_min_price
        self.xau_max = self.settings.instrument.xauusd_max_price

    def classify(self, message: Message) -> ClassificationResult:
        text = (message.text or "").strip()
        if not text:
            return ClassificationResult(
                message_class=MessageClass.NOISE,
                confidence=1.0,
                reasons=["empty_text"],
            )

        signal_score, signal_reasons = self._signal_score(text)
        followup_score, followup_reasons = self._followup_score(text, message)
        noise_score, noise_reasons = self._noise_score(text)

        scores = {
            MessageClass.SIGNAL: signal_score,
            MessageClass.FOLLOW_UP: followup_score,
            MessageClass.NOISE: noise_score,
        }
        all_reasons = {
            MessageClass.SIGNAL: signal_reasons,
            MessageClass.FOLLOW_UP: followup_reasons,
            MessageClass.NOISE: noise_reasons,
        }

        winner = max(scores, key=lambda k: scores[k])
        winner_score = scores[winner]
        runners = sorted(scores.values(), reverse=True)
        margin = runners[0] - runners[1] if len(runners) > 1 else runners[0]

        confidence = min(1.0, max(0.0, 0.5 + margin / 2.0))
        if winner_score <= 0:
            return ClassificationResult(
                message_class=MessageClass.UNCERTAIN,
                confidence=0.0,
                reasons=["no_evidence"],
            )

        if confidence < self.threshold:
            top_reasons = [f"score_{k.value.lower()}={v:.2f}" for k, v in scores.items()]
            return ClassificationResult(
                message_class=MessageClass.UNCERTAIN,
                confidence=confidence,
                reasons=top_reasons + all_reasons[winner],
            )

        return ClassificationResult(
            message_class=winner,
            confidence=confidence,
            reasons=all_reasons[winner],
        )

    def _signal_score(self, text: str) -> tuple[float, list[str]]:
        """Score the signal-likeness of `text`. Returns (score in [0, 1+], reasons)."""
        reasons: list[str] = []
        score = 0.0

        has_instrument = bool(_INSTRUMENT_RE.search(text))
        has_direction = bool(_DIRECTION_RE.search(text))
        plausible_prices = self._plausible_xau_prices(text)
        has_sl = bool(_SL_TOKEN_RE.search(text))
        has_tp = bool(_TP_TOKEN_RE.search(text) or _TAKE_PROFIT_RE.search(text))
        has_entry_kw = bool(_ENTRY_TOKEN_RE.search(text))

        if has_instrument:
            score += 0.30
            reasons.append("has_instrument")
        if has_direction:
            score += 0.30
            reasons.append("has_direction")
        if plausible_prices >= 1:
            score += 0.10
            reasons.append(f"plausible_prices={plausible_prices}")
        if has_sl:
            score += 0.20
            reasons.append("has_sl")
        if has_tp:
            score += 0.20
            reasons.append("has_tp")
        if has_entry_kw:
            score += 0.05
            reasons.append("has_entry_kw")

        # A clear outcome verb without a TP/SL price line strongly suggests follow-up.
        if _OUTCOME_VERB_RE.search(text) and not (has_sl and has_tp):
            score -= 0.40
            reasons.append("outcome_verb_penalty")

        # Promo content suppresses signal score.
        if _PROMO_RE.search(text) or _GOLD_PROMO_HINT_RE.search(text):
            score -= 0.50
            reasons.append("promo_penalty")
        if _URL_RE.search(text):
            score -= 0.15
            reasons.append("url_penalty")

        return max(0.0, score), reasons

    def _followup_score(self, text: str, message: Message) -> tuple[float, list[str]]:
        reasons: list[str] = []
        score = 0.0

        has_outcome_verb = bool(_OUTCOME_VERB_RE.search(text))
        has_pips = bool(_PIPS_PROFIT_RE.search(text))
        has_be = bool(_BE_RE.search(text))
        has_cancel = bool(_CANCEL_RE.search(text))
        is_reply = message.reply_to_msg_id is not None
        plausible_prices = self._plausible_xau_prices(text)

        # Strong signals must contain BOTH SL and TP price lines, never just one.
        has_sl_price = bool(_SL_TOKEN_RE.search(text))
        has_tp_price = bool(_TP_TOKEN_RE.search(text) or _TAKE_PROFIT_RE.search(text))
        full_signal_form = has_sl_price and has_tp_price

        if has_outcome_verb:
            score += 0.45
            reasons.append("outcome_verb")
        if has_pips:
            score += 0.30
            reasons.append("pips_mention")
        if has_be:
            score += 0.30
            reasons.append("break_even")
        if has_cancel:
            score += 0.35
            reasons.append("cancel_signal")
        if is_reply:
            score += 0.10
            reasons.append("is_reply")

        # If the message ALREADY looks like a complete signal, suppress the follow-up score.
        if full_signal_form:
            score -= 0.50
            reasons.append("full_signal_form_penalty")

        # Lone numbers like "1.0" or "2.0" should never be follow-ups by themselves.
        if not has_outcome_verb and not has_pips and not has_be and not has_cancel:
            score -= 0.30
            reasons.append("no_outcome_evidence")

        # An XAUUSD-plausible price + outcome verb is the canonical "TP1 hit @ 4710" pattern.
        if has_outcome_verb and plausible_prices >= 1:
            score += 0.10
            reasons.append("outcome_plus_price")

        return max(0.0, score), reasons

    def _noise_score(self, text: str) -> tuple[float, list[str]]:
        reasons: list[str] = []
        score = 0.0

        if _PROMO_RE.search(text):
            score += 0.55
            reasons.append("promo")
        if _GOLD_PROMO_HINT_RE.search(text):
            score += 0.40
            reasons.append("token_promo")
        if _URL_RE.search(text):
            score += 0.30
            reasons.append("url")
        if len(text) <= 5:
            score += 0.50
            reasons.append("very_short")
        # Pure emoji / non-alphanumeric content.
        if not re.search(r"[A-Za-z0-9]", text):
            score += 0.60
            reasons.append("no_alphanumeric")

        # Strong signal evidence cancels noise unless promo overwhelms.
        if _DIRECTION_RE.search(text) and _INSTRUMENT_RE.search(text):
            score -= 0.35
            reasons.append("signal_evidence_present")

        return max(0.0, score), reasons

    def _plausible_xau_prices(self, text: str) -> int:
        count = 0
        for match in _PRICE_RE.finditer(text):
            try:
                value = float(match.group(0))
            except ValueError:
                continue
            if self.xau_min <= value <= self.xau_max:
                count += 1
        return count
