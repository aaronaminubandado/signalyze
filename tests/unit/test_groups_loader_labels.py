"""Tests for the group label resolver used by reporting surfaces."""

from __future__ import annotations

from pathlib import Path

from signalyze.ingest import build_label_map, resolve_group_label


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_build_label_map_extracts_id_to_label(tmp_path: Path) -> None:
    groups_file = _write(
        tmp_path / "groups.txt",
        "Alpha Signals    | id: -100123 | username: alpha\n"
        "Beta             | id: -100456 | username: None\n"
        "# comment\n"
        "Lone Username    | username: orphan\n",
    )
    mapping = build_label_map(groups_file)
    assert mapping == {"-100123": "Alpha Signals", "-100456": "Beta"}


def test_build_label_map_missing_file_is_empty(tmp_path: Path) -> None:
    assert build_label_map(tmp_path / "does-not-exist.txt") == {}


def test_resolve_group_label_falls_back_to_id() -> None:
    mapping = {"-100123": "Alpha"}
    assert resolve_group_label("-100123", mapping) == "Alpha"
    assert resolve_group_label("-100999", mapping) == "-100999"


def test_resolve_group_label_truncates_long_labels() -> None:
    mapping = {"-1": "X" * 50}
    out = resolve_group_label("-1", mapping, max_len=10)
    assert len(out) == 10
    assert out.endswith("…")


def test_resolve_group_label_max_len_zero_disables_truncation() -> None:
    mapping = {"-1": "X" * 80}
    assert resolve_group_label("-1", mapping, max_len=0) == "X" * 80
