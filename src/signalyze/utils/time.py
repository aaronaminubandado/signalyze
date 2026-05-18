"""Time helpers. All timestamps in the system are UTC ISO8601 strings ending in 'Z'."""

from __future__ import annotations

from datetime import UTC, datetime


def to_utc(value: datetime) -> datetime:
    """Return `value` as a tz-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Format a datetime as ISO8601 with trailing 'Z'."""
    return to_utc(value).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """Parse an ISO8601 string (with optional 'Z') into a UTC datetime."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(UTC)


def now_utc_iso() -> str:
    """Return the current UTC time as ISO8601 'Z' string."""
    return format_utc(datetime.now(tz=UTC))
