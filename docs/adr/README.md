# Architecture Decision Records

Each ADR captures one significant design decision, the alternatives considered,
and the trade-offs that drove the choice. Records are immutable once accepted;
later decisions supersede earlier ones explicitly.

| #   | Title                                                          | Status   |
| --- | -------------------------------------------------------------- | -------- |
| 001 | Rebuild from scratch (reuse raw CSVs + Telegram fetcher)       | Accepted |
| 002 | SQLite-canonical, Parquet-raw, file-cached LLM responses       | Accepted |
| 003 | Rules-first parsing with cached LLM fallback                   | Accepted |
| 004 | Four-tier linker with explicit confidence scores               | Accepted |
| 005 | Walk-forward simulator with first-touch + same-bar ambiguity   | Accepted |

Template: copy `template.md` (TBD) for new entries.
