"""Golden-set exact-match evaluation for the signal parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signalyze.parse.signals_rules import SignalRuleParser

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "golden_signals.jsonl"
)


def _load() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _matches(record: dict[str, object], parsed: object | None) -> tuple[bool, str]:
    expected_direction = record.get("direction")
    if expected_direction is None:
        return parsed is None, f"expected reject, got parsed={parsed is not None}"

    if parsed is None:
        return False, "expected parsed signal, got None"

    assert hasattr(parsed, "direction")
    if parsed.direction.value != expected_direction:
        return False, f"direction {parsed.direction.value} != {expected_direction}"
    if record.get("entry") is not None and parsed.entry != record["entry"]:
        return False, f"entry {parsed.entry} != {record['entry']}"
    if record.get("entry_low") is not None and parsed.entry_low != record["entry_low"]:
        return False, f"entry_low {parsed.entry_low} != {record['entry_low']}"
    if record.get("entry_high") is not None and parsed.entry_high != record["entry_high"]:
        return False, f"entry_high {parsed.entry_high} != {record['entry_high']}"
    if "stop_loss" in record and parsed.stop_loss != record.get("stop_loss"):
        return False, f"stop_loss {parsed.stop_loss} != {record.get('stop_loss')}"
    expected_tps = record.get("take_profits", [])
    if parsed.take_profits != expected_tps:
        return False, f"tps {parsed.take_profits} != {expected_tps}"
    if "quality_flag" in record and parsed.quality_flag.value != record["quality_flag"]:
        return False, f"quality {parsed.quality_flag.value} != {record['quality_flag']}"
    return True, ""


@pytest.mark.golden
def test_golden_signal_extraction_exact_match_threshold() -> None:
    parser = SignalRuleParser()
    records = _load()
    matches = 0
    failures: list[str] = []

    for r in records:
        result = parser.parse_text(str(r["text"]))
        ok, why = _matches(r, result.payload)
        if ok:
            matches += 1
        else:
            failures.append(f"{r['id']}: {why}")

    exact_match = matches / len(records)
    assert exact_match >= 0.90, (
        f"Exact match {exact_match:.2f} < 0.90. Failures:\n" + "\n".join(failures)
    )
