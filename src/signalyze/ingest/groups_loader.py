"""Parse `config/groups.txt` into typed `GroupTarget` records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from signalyze.utils.logging import get_logger

logger = get_logger("signalyze.ingest.groups_loader")


@dataclass(frozen=True)
class GroupTarget:
    """A single Telegram group reference parsed from groups.txt."""

    label: str
    target: str  # numeric id (as string) or username


def parse_groups_file(groups_file: Path) -> list[GroupTarget]:
    """Parse a groups.txt file into deduplicated `GroupTarget` records.

    Supported line styles:
        `Label | id: -100123 | username: foo`  (current format)
        `@foo` or `foo`
        `-100123`
        Lines starting with `#` are comments.
    """
    if not groups_file.exists():
        raise FileNotFoundError(f"Groups file not found: {groups_file}")

    targets: list[GroupTarget] = []
    seen: set[str] = set()

    for line_number, raw_line in enumerate(
        groups_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        parsed = _parse_line(raw_line)
        if parsed is None:
            stripped = raw_line.strip()
            if stripped and not stripped.startswith("#"):
                logger.warning("Skipping unparsable group entry on line %d: %s", line_number, stripped)
            continue

        key = _dedupe_key(parsed.target)
        if key in seen:
            logger.info("Skipping duplicate target '%s' (%s)", parsed.label, parsed.target)
            continue
        seen.add(key)
        targets.append(parsed)

    return targets


def _parse_line(raw_line: str) -> GroupTarget | None:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    label = stripped.split("|", 1)[0].strip()
    id_match = re.search(r"\bid:\s*(-?\d+)\b", stripped, flags=re.IGNORECASE)
    username_match = re.search(
        r"\busername:\s*([A-Za-z0-9_@]+|None|N/A)\b",
        stripped,
        flags=re.IGNORECASE,
    )

    target: str | None = None
    if username_match:
        username = username_match.group(1).strip()
        if username.lower() not in {"none", "n/a", "na"}:
            target = username.lstrip("@")

    if target is None and id_match:
        target = id_match.group(1)

    if target is None:
        fallback = stripped
        if "|" in fallback:
            fallback = fallback.split("|")[-1].strip()
        target = fallback.lstrip("@")

    if not target:
        return None

    return GroupTarget(label=label or target, target=target)


def _dedupe_key(target: str) -> str:
    cleaned = target.strip().lstrip("@")
    if re.fullmatch(r"-?\d+", cleaned):
        return str(int(cleaned))
    return cleaned.lower()


def coerce_entity_target(target: str) -> int | str:
    """Convert a CLI target string into the form Telethon expects."""
    cleaned = target.strip().lstrip("@")
    if re.fullmatch(r"-?\d+", cleaned):
        return int(cleaned)
    return cleaned


def build_label_map(groups_file: Path) -> dict[str, str]:
    """Return a `{group_id: label}` mapping for every entry with an `id:` field.

    Missing files yield an empty map (callers fall back to the raw `group_id`).
    Used by reporting surfaces to display human-readable channel names instead
    of opaque numeric Telegram IDs.
    """
    if not groups_file.exists():
        return {}

    mapping: dict[str, str] = {}
    for raw_line in groups_file.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        id_match = re.search(r"\bid:\s*(-?\d+)\b", stripped, flags=re.IGNORECASE)
        if not id_match:
            continue
        label = stripped.split("|", 1)[0].strip()
        if not label:
            continue
        mapping[id_match.group(1)] = label
    return mapping


def resolve_group_label(
    group_id: str,
    label_map: dict[str, str],
    *,
    max_len: int = 36,
) -> str:
    """Return the human-readable label for `group_id`, falling back to the id.

    `max_len` truncates over-long labels (with an ellipsis) so they fit into
    fixed-width CLI tables without breaking alignment.
    """
    label = label_map.get(group_id) or group_id
    if max_len > 0 and len(label) > max_len:
        return label[: max_len - 1] + "…"
    return label
