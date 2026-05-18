# ADR 001 — Rebuild from scratch, reuse raw CSVs and the Telegram fetcher

- **Status:** Accepted
- **Date:** 2026-04
- **Context:** A prototype existed with a Telegram fetcher, a regex-based signal
  extractor, and several months of collected raw CSVs.

## Decision

Restart the project as a clean, modular package. **Reuse** the raw CSV snapshots
in `data/raw/` and the Telegram fetcher logic. **Discard** the regex extractor
and the ad-hoc project layout.

## Why

- The collected data is the most expensive thing in the project; rebuilding it
  would mean weeks of waiting for new messages and would not be reproducible.
- The fetcher uses a stable Telethon API and proved correct on real data.
- The regex extractor, by contrast, produced systematically wrong outputs:
  `MISSING_SL_TP` was the modal quality flag, entries like `1.0` / `2.0`
  appeared, and follow-up messages were frequently misclassified as new signals.
- The flat project layout (`src/parsing/extractor.py`, `main.py`) made it
  impossible to add layers without further entanglement.

## Alternatives considered

1. **Patch the existing code in place.** Rejected: the extractor's failure mode
   was not a single bug but the absence of a layered architecture (no domain
   model, no confidence scoring, no follow-up linking).
2. **Full restart including data collection.** Rejected: the raw CSVs are
   immutable historical evidence; throwing them away would be wasteful and
   would prevent comparing reported vs actual performance over the same period.

## Consequences

- A `src/signalyze/` package with explicit `domain/storage/ingest/classify/
  parse/link/evaluate/...` layers becomes the only entry point.
- A `signalyze ingest backfill` command re-ingests the legacy CSVs into the new
  canonical SQLite schema, so the new pipeline runs against the same data.
- The Telethon fetcher is preserved as `signalyze.ingest.telegram_fetcher`, with
  the same `groups.txt` configuration file.
- Future work (LLM fallback, market data, simulator) can be added as new
  pipeline stages without touching ingestion.
