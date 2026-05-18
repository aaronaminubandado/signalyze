"""Per-message classification: SIGNAL vs FOLLOW_UP vs NOISE vs UNCERTAIN."""

from signalyze.classify.rules import ClassificationResult, RuleClassifier

__all__ = ["ClassificationResult", "RuleClassifier"]
