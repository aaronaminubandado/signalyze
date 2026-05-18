"""Golden-set exact-match evaluation for the follow-up parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signalyze.parse.follow_ups_rules import FollowUpRuleParser

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "golden_follow_ups.jsonl"
)


def _load() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _matches(record: dict[str, object], parsed: object | None) -> tuple[bool, str]:
    expected_event = record.get("event_type")
    if expected_event is None:
        return parsed is None, f"expected reject, got parsed={parsed is not None}"
    if parsed is None:
        return False, "expected parsed follow-up, got None"

    if parsed.event_type.value != expected_event:
        return False, f"event {parsed.event_type.value} != {expected_event}"
    if "tp_index" in record and parsed.tp_index != record["tp_index"]:
        return False, f"tp_index {parsed.tp_index} != {record['tp_index']}"
    if "new_stop_loss" in record and parsed.new_stop_loss != record["new_stop_loss"]:
        return False, f"new_stop_loss {parsed.new_stop_loss} != {record['new_stop_loss']}"
    if "claimed_pips" in record and parsed.claimed_pips != record["claimed_pips"]:
        return False, f"claimed_pips {parsed.claimed_pips} != {record['claimed_pips']}"
    if "claimed_price" in record and parsed.claimed_price != record["claimed_price"]:
        return False, f"claimed_price {parsed.claimed_price} != {record['claimed_price']}"
    return True, ""


@pytest.mark.golden
def test_follow_ups_golden_exact_match_threshold() -> None:
    parser = FollowUpRuleParser()
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

    score = matches / len(records)
    assert score >= 0.95, (
        f"Follow-up exact match {score:.2f} < 0.95. Failures:\n" + "\n".join(failures)
    )
