"""Compare reported and actual outcomes, surface discrepancies, compute analytics."""

from signalyze.compare.discrepancy import (
    DiscrepancyCategory,
    DiscrepancyRow,
    compute_discrepancies,
)

__all__ = ["DiscrepancyCategory", "DiscrepancyRow", "compute_discrepancies"]
