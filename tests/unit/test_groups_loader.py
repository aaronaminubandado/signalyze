"""Groups loader: parses the existing groups.txt format."""

from __future__ import annotations

from pathlib import Path

from signalyze.ingest.groups_loader import coerce_entity_target, parse_groups_file


def test_parse_groups_file_handles_id_and_username(tmp_path: Path) -> None:
    f = tmp_path / "groups.txt"
    f.write_text(
        """
# comment line
Foo Group                | id: -1001234567 | username: foo_signals
Bar Group                | id: -1009876543 | username: None
@plain_username
-1000111222
""".strip(),
        encoding="utf-8",
    )

    targets = parse_groups_file(f)
    labels = [t.label for t in targets]
    values = [t.target for t in targets]

    assert "Foo Group" in labels
    assert "foo_signals" in values
    assert "-1009876543" in values
    assert "plain_username" in values
    assert "-1000111222" in values


def test_parse_groups_dedupes(tmp_path: Path) -> None:
    f = tmp_path / "g.txt"
    f.write_text(
        """
A | id: -1001 | username: same_user
B | id: -1002 | username: same_user
""".strip(),
        encoding="utf-8",
    )
    targets = parse_groups_file(f)
    assert len(targets) == 1


def test_coerce_entity_target_distinguishes_id_and_username() -> None:
    assert coerce_entity_target("-1001234567") == -1001234567
    assert coerce_entity_target("@foo") == "foo"
    assert coerce_entity_target("bar") == "bar"
