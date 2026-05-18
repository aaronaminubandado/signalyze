"""SQLite connection helpers and migration runner."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from signalyze.utils.logging import get_logger
from signalyze.utils.time import now_utc_iso

logger = get_logger("signalyze.storage.db")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    """Thin sqlite3 wrapper with WAL mode, foreign keys, and row_factory enabled."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def open_database(path: Path) -> Database:
    """Open (and ensure schema for) the canonical Signalyze database."""
    db = Database(path)
    ensure_schema(db)
    return db


def ensure_schema(db: Database) -> None:
    """Run any pending migration SQL files in lexicographic order."""
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
    """)

    applied = {row["version"] for row in db.conn.execute("SELECT version FROM schema_version")}
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    for migration in migration_files:
        version = int(migration.name.split("_", 1)[0])
        if version in applied:
            continue

        sql = migration.read_text(encoding="utf-8")
        with db.transaction() as conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, now_utc_iso()),
            )
        logger.info("Applied migration %s", migration.name)
