"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from signalyze.storage import Database, open_database


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Database:
    """Open a fresh, schema-migrated SQLite database under pytest's tmp dir."""
    return open_database(tmp_path / "signalyze.sqlite")
