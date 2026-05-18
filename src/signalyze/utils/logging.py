"""Centralized logger configuration."""

from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logger(name: str = "signalyze", level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger; idempotent across re-imports."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the root signalyze logger."""
    setup_logger()
    return logging.getLogger(name)
