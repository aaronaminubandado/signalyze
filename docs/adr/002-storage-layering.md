# ADR 002 — Three-tier storage: SQLite canonical, Parquet raw, SQLite LLM cache

- **Status:** Accepted
- **Date:** 2026-04

## Decision

Use **three** distinct storage layers, each with a single responsibility:

| Layer            | Format           | Path                          | Purpose                                                       |
| ---------------- | ---------------- | ----------------------------- | ------------------------------------------------------------- |
| Canonical store  | SQLite + WAL     | `data/db/signalyze.sqlite`    | All entities. Indexes, joins, transactional updates.          |
| Raw snapshots    | Parquet (1/group)| `data/raw/<group>.parquet`    | Immutable archive of every fetched message. Append-only.      |
| LLM response cache | SQLite          | `data/cache/llm_cache.sqlite` | Keyed by `(model, prompt_version, content_hash)`; replay-only.|

Migrations are explicit SQL files under `src/signalyze/storage/migrations/`,
applied through a small `schema_version` table.

## Why

- **Canonical store is queryable and transactional.** Domain models map 1-to-1
  to tables. Foreign keys, indexes, and `UNIQUE` constraints enforce invariants
  (one parsed `Signal` per `Message`, one `ReportedOutcome` per `Signal`, etc.).
- **Raw snapshots are immutable.** Parquet is column-oriented and easy to grep
  with pandas/polars for ad-hoc investigation, and re-ingestion from the raw
  Parquet is always possible if the canonical schema changes.
- **LLM cache makes runs reproducible and cheap.** Replays cost nothing and
  guarantee the same parser output for the same inputs at the same prompt
  version. Every cached row records `usage_tokens` + `estimated_usd` so cost
  audits are trivial.

## Alternatives considered

1. **Single Postgres database.** Overkill for a single-user analytical tool;
   adds an operational burden and removes the trivial "delete `data/` to
   reset" property.
2. **DuckDB or Polars-on-Parquet only.** Considered but rejected because we
   need cheap, transactional upserts during pipeline runs (linker, evaluator),
   which DuckDB does not yet handle cleanly.
3. **No LLM cache.** Rejected: a single re-run would cost money and produce
   different parses, undermining the determinism principle.

## Consequences

- Everything in `data/` is gitignored; the project can be cloned and rebuilt
  end-to-end from `groups.txt` and `data/raw/`.
- The DB is the source of truth for downstream stages. Raw Parquet is never
  read by the runtime pipeline, only by humans and one-off scripts.
- Schema changes require a new numbered migration in
  `src/signalyze/storage/migrations/` and a bump to `schema_version`.
