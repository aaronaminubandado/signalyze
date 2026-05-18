"""Orchestrate classification: rules first, LLM fallback for UNCERTAIN, persist to DB."""

from __future__ import annotations

from dataclasses import dataclass

from signalyze.classify.llm_fallback import llm_classify
from signalyze.classify.rules import ClassificationResult, RuleClassifier
from signalyze.domain import Message, MessageClass, MessageClassification
from signalyze.llm import LLMClient
from signalyze.storage import Database
from signalyze.storage.repositories import upsert_classification
from signalyze.utils.logging import get_logger
from signalyze.utils.time import now_utc_iso

logger = get_logger("signalyze.classify.runner")


@dataclass
class ClassifyStats:
    """Summary of one classification run."""

    total: int = 0
    by_class: dict[str, int] | None = None
    rules_decisions: int = 0
    llm_decisions: int = 0
    uncertain: int = 0

    def __post_init__(self) -> None:
        if self.by_class is None:
            self.by_class = {c.value: 0 for c in MessageClass}


def classify_messages(
    *,
    db: Database,
    messages: list[Message],
    rule_classifier: RuleClassifier,
    llm_client: LLMClient | None = None,
    use_llm: bool = True,
) -> ClassifyStats:
    """Classify the given messages and upsert each into `message_classifications`."""
    stats = ClassifyStats()
    classifier_version = rule_classifier.version

    for message in messages:
        stats.total += 1
        result, method = _classify_one(
            message=message,
            rule_classifier=rule_classifier,
            llm_client=llm_client if use_llm else None,
        )
        if method == "rules":
            stats.rules_decisions += 1
        else:
            stats.llm_decisions += 1
        if result.message_class == MessageClass.UNCERTAIN:
            stats.uncertain += 1
        assert stats.by_class is not None
        stats.by_class[result.message_class.value] += 1

        classification = MessageClassification(
            message_uid=message.message_uid,
            **{"class": result.message_class},
            confidence=result.confidence,
            method=method,
            reasons=result.reasons,
            classifier_version=classifier_version,
            classified_at=now_utc_iso(),
        )
        with db.transaction() as conn:
            upsert_classification(conn, classification)

    return stats


def _classify_one(
    *,
    message: Message,
    rule_classifier: RuleClassifier,
    llm_client: LLMClient | None,
) -> tuple[ClassificationResult, str]:
    rule_result = rule_classifier.classify(message)
    if rule_result.message_class != MessageClass.UNCERTAIN:
        return rule_result, "rules"

    if llm_client is None or not llm_client.is_available:
        return rule_result, "rules"

    llm_result = llm_classify(message, llm_client)
    if llm_result is None:
        return rule_result, "rules"
    return llm_result, "llm"
