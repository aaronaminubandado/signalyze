"""Analytics: per-group / per-period metrics on top of the canonical store."""

from signalyze.analytics.metrics import (
    GroupMetrics,
    compute_group_metrics,
    iter_group_metrics,
)

__all__ = [
    "GroupMetrics",
    "compute_group_metrics",
    "iter_group_metrics",
]
