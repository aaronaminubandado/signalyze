"""Persistent cache for LLM calls. Keyed by `(model, prompt_version, content_hash)`."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from signalyze.utils.time import now_utc_iso


class LLMCache:
    """Tiny SQLite-backed key/value cache that survives across runs."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_cache (
                cache_key TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                response_json TEXT NOT NULL,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def make_key(model: str, prompt_version: str, content: str) -> str:
        """Stable cache key based on the canonical (lowercased, whitespace-collapsed) content."""
        normalized = " ".join(content.lower().split())
        h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        return f"{model}:{prompt_version}:{h}"

    def get(self, key: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT response_json FROM llm_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        result = json.loads(row[0])
        if not isinstance(result, dict):
            return None
        return result

    def set(
        self,
        key: str,
        *,
        model: str,
        prompt_version: str,
        content_hash: str,
        response: dict[str, object],
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO llm_cache (
                cache_key, model, prompt_version, content_hash, response_json,
                tokens_in, tokens_out, cost_usd, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                model,
                prompt_version,
                content_hash,
                json.dumps(response),
                tokens_in,
                tokens_out,
                cost_usd,
                now_utc_iso(),
            ),
        )
        self._conn.commit()

    def total_cost_usd(self) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_cache"
        ).fetchone()
        return float(row[0]) if row is not None else 0.0
