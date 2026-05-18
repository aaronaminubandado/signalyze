"""Golden-set evaluation of the rule-based classifier.

The plan's Phase 2 verifiable output is `>= 0.90` precision on the SIGNAL class
against the golden fixtures (later hardened to 0.95).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from signalyze.classify import RuleClassifier
from signalyze.domain import Message, MessageClass

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "golden_classifications.jsonl"
)


def _load_golden() -> list[dict[str, str]]:
    return [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def _to_message(record: dict[str, str]) -> Message:
    return Message(
        message_uid=record["message_uid"],
        group_id=record["message_uid"].split(":", 1)[0],
        message_id=1,
        timestamp_utc="2026-01-17T10:00:00Z",
        text=record["text"],
        ingested_at="2026-01-17T10:00:00Z",
        ingest_method="csv_backfill",
    )


@pytest.mark.golden
def test_signal_precision_meets_threshold() -> None:
    classifier = RuleClassifier()
    records = _load_golden()
    confusion: defaultdict[tuple[str, str], int] = defaultdict(int)

    for r in records:
        msg = _to_message(r)
        result = classifier.classify(msg)
        confusion[(r["expected"], result.message_class.value)] += 1

    # Precision for SIGNAL = TP / (TP + FP)
    tp = confusion[("SIGNAL", "SIGNAL")]
    fp = sum(v for (truth, pred), v in confusion.items() if pred == "SIGNAL" and truth != "SIGNAL")
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    assert precision >= 0.90, f"SIGNAL precision {precision:.2f} < 0.90 (confusion={dict(confusion)})"


@pytest.mark.golden
def test_no_full_signal_misclassified_as_followup() -> None:
    """The previous prototype's biggest bug: TP-hit follow-ups treated as new signals.
    Conversely, this guard ensures classifier never silently classifies a complete
    signal payload (direction + SL price + TP price) as FOLLOW_UP or NOISE.
    """
    classifier = RuleClassifier()
    records = _load_golden()
    for r in records:
        if r["expected"] != "SIGNAL":
            continue
        result = classifier.classify(_to_message(r))
        assert result.message_class in {MessageClass.SIGNAL, MessageClass.UNCERTAIN}, (
            f"Signal misclassified as {result.message_class.value} for {r['message_uid']}"
        )
