# Signalyze

A modular pipeline that turns Telegram trading-signal channels into auditable,
reproducible performance analytics — including a reported-vs-actual comparison
layer that exposes systematic publisher bias.

> **v1 scope:** XAUUSD, batch/historical analysis. Multi-instrument, live
> ingestion, and tick-resolution simulation are tracked as follow-on work.

## At a glance

A single end-to-end run produces, per stage:

| Stage             | Output                                                          |
| ----------------- | --------------------------------------------------------------- |
| ingest            | messages (Telethon live or raw-CSV backfill)                    |
| classify          | classifications (SIGNAL / FOLLOW_UP / NOISE / UNCERTAIN)        |
| parse signals     | structured signals with `COMPLETE` / `PARTIAL` quality          |
| parse follow-ups  | typed follow-up events (TP_HIT, SL_HIT, MOVE_SL, CANCEL, …)     |
| link              | signal ↔ follow-up links via `reply_to` / temporal-numeric / LLM tiebreak |
| evaluate reported | reported outcomes derived from each signal's linked follow-ups  |
| market            | XAUUSD OHLCV bars (Twelve Data, gap-aware fetcher)              |
| evaluate actual   | actual outcomes from a walk-forward, first-touch simulator      |
| compare           | per-signal discrepancies (reported vs actual)                   |
| report            | leaderboard, TP-depth breakdown, Streamlit dashboard, static HTML |

The point of running the whole pipeline is the **comparison layer**: most
signal channels publish very high reported win rates (the chat threads are
edited to mark each trade as a win after the fact), and Signalyze quantifies
the gap between those self-reported outcomes and what a mechanical execution
of the same signals would have produced on the same OHLCV bars.

To keep the public repo neutral, no real channel labels or Telegram peer ids
are committed. `config/groups.example.txt` ships with anonymous placeholders
(`Channel A`, `Channel B`, …); copy it to `config/groups.txt` (gitignored) and
fill in your own. The CLI, HTML report, and Streamlit dashboard read those
labels via `groups_loader.resolve_group_label`, so every surface stays
human-readable without leaking the manifest.

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev,llm,report]"
cp .env.example .env  # fill API_ID, API_HASH, OPENAI_API_KEY, TWELVEDATA_API_KEY

signalyze init-db
signalyze ingest backfill                          # load data/raw/*.csv into messages
signalyze classify run --no-llm                    # SIGNAL / FOLLOW_UP / NOISE / UNCERTAIN
signalyze parse signals --no-llm
signalyze parse follow-ups --no-llm
signalyze link run --no-llm
signalyze evaluate reported
signalyze evaluate leaderboard                     # per-group reported win rates
signalyze evaluate tp-depth --min-signals 30       # per-group TPn hit rates

signalyze market fetch --instrument XAUUSD --interval 1min
signalyze evaluate actual
signalyze compare run
signalyze report html
streamlit run src/signalyze/report/streamlit_app.py
```

Drop `--no-llm` on any stage to enable the LLM fallback (cached, budget-capped).

## Pipeline

```
config/groups.txt (your private manifest) + data/raw/*.csv
  -> ingest    (Telethon + raw CSV backfill   -> messages)
  -> classify  (rules + LLM fallback          -> message_classifications)
  -> parse     (rules + LLM fallback          -> signals, follow_ups)
  -> link      (reply_to / temporal-numeric / recent-open / LLM tiebreak)
  -> evaluate  (linked follow-ups             -> reported_outcomes)
  -> market    (Twelve Data, idempotent       -> market_bars)
  -> evaluate  (walk-forward, first-touch sim -> actual_outcomes)
  -> compare   (reported vs actual            -> discrepancies)
  -> report    (Streamlit + static HTML)
```

See [`docs/architecture.md`](docs/architecture.md) for the per-stage I/O
contract, and [`docs/adr/`](docs/adr/) for the five core design decisions.

## Design principles

- **Domain-first.** Pydantic v2 models in `signalyze.domain` are pure data;
  storage and parsing depend on domain, never the reverse.
- **Idempotent stages.** Every stage re-runs cleanly. Outputs carry
  `parse_version` / `linker_version` / `computed_version` so a version bump is
  distinguishable from a no-op replay.
- **Deterministic-first, LLM-fallback.** Rules emit
  `(payload, confidence, reasons)`. The LLM fires only below
  `parse.llm_escalation_threshold`, and every call is cached on
  `(model, prompt_version, content_hash)`.
- **Confidence everywhere.** Classifications, parses, and links each carry a
  confidence score. Headline metrics filter on it.
- **Cost discipline.** Hard budget cap via `SIGNALYZE_LLM_MAX_USD_PER_RUN`
  (default $2); cache hits are free.

## Repository layout

```
signalyze/
  config/            # settings.toml + groups.example.txt (real groups.txt is gitignored)
  src/signalyze/     # package
    domain/          # pydantic v2 models (zero I/O)
    storage/         # SQLite + Parquet + LLM cache, with SQL migrations
    utils/           # time (UTC), money (pips), logging
    llm/             # provider-agnostic client + cost cap + cache
    ingest/          # Telethon fetcher + raw CSV backfill
    classify/        # rules + LLM fallback + runner
    parse/           # signal & follow-up extraction (rules + LLM + runner)
    link/            # tiered linker + LLM tiebreaker + review CSV export
    market/          # OHLCV provider protocol + Twelve Data + CSV
    evaluate/        # reported & actual (walk-forward) outcomes
    compare/         # reported-vs-actual discrepancies
    analytics/       # win rate, RR, time-to-hit, expectancy
    report/          # Streamlit dashboard + static HTML export
    cli.py           # Typer entry point
  tests/
    unit/            # ~50 unit tests; rules, storage, simulator
    golden/          # exact-match thresholds enforced in CI
    fixtures/        # hand-labelled JSONL golden sets
  docs/
    architecture.md
    adr/             # 5 accepted ADRs
  data/              # gitignored: raw/, db/, cache/, reports/
```

## Quality gates (CI)

- `ruff` (lint) + `mypy --strict` (typecheck) on `src/signalyze`.
- `pytest` with the golden thresholds bumped to **≥0.95** after Phase 11
  hardening for signal extraction, follow-up extraction, and SIGNAL-class
  precision in classification.
- Cost guard exercised by tests for the LLM cache and the budget cap.

## License

MIT.
