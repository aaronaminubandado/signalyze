# Signalyze

A **prototype** modular pipeline that turns Telegram XAUUSD trading-signal
channels into batch analytics: parse signals and follow-ups, link them,
derive *reported* outcomes from the chat, simulate *actual* first-touch
outcomes on OHLCV bars, and compare the two.

> **Status:** exploratory / prototype. Much of the pipeline was
> built quickly with AI assistance. Stages run and tests pass, but several
> correctness gaps remain (see [Known limitations](#known-limitations)).
> Treat numbers as directional, not audited research.

> **v1 scope:** XAUUSD, batch/historical analysis only. Not financial advice.

## At a glance

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

The comparison layer is the interesting idea: channels often show very high
*self-reported* win rates, and Signalyze tries to measure the gap versus a
mechanical execution of the same parsed signals on the same bars. That gap is
only as good as parsing, linking, and market coverage — see limitations below.

To keep the public repo neutral, no real channel labels or Telegram peer ids
are committed. `config/groups.example.txt` ships with anonymous placeholders
(`Channel A`, `Channel B`, …); copy it to `config/groups.txt` (gitignored) and
fill in your own. The CLI, HTML report, and Streamlit dashboard read those
labels via `groups_loader.resolve_group_label`.

## Known limitations

Honest caveats before you trust or demo the metrics:

### Linking
- Schema allows **more than one link per follow-up** (`UNIQUE(follow_up_id, signal_id)` only). Re-running the linker after signals change can attach the same follow-up to multiple parents and double-count outcomes.
- LLM tiebreak currently sends **empty follow-up text**, so the model cannot read the message. An LLM “none of these” answer is ignored; the top heuristic candidate wins.
- Temporal linking does **not** filter to open trades or matching direction the way the docs suggest; `tp_index` boosts score without checking claimed price against that TP level.

### Parsing
- Entry extraction can mistake a tight **TP price range** for an entry zone, or take the **first price** on a direction line (e.g. SL before ENTRY).
- First `BUY`/`SELL` token wins (no negation handling). Take-profit list order is appearance order, not necessarily TP1→TPn.
- Follow-up LLM fallback can turn non-events into structured events when rules return nothing.

### Evaluation
- Reported `claimed_pips` **sums** per-TP pip claims; many channels post **cumulative** figures, so totals are often inflated.
- Actual sim: `win_policy` is stored on outcomes but **does not change** simulation logic (always first touched TP among levels).
- Range entries fill mid-band on the first intersecting bar, but the SL/TP walk still starts at the **signal timestamp** (pre-fill bars can decide the trade).
- Sparse or truncated bar caches tend to become `OPEN_AT_EXPIRY` rather than `INSUFFICIENT_DATA`. Market fetch treats a day as covered at **~50%** of expected 1m bars.
- Signal `quality_flag` is largely ignored when computing outcomes. Follow-up BE/SL moves are not applied in the actual simulator.
- Reported vs actual win-rate **gap** can mix different decided sets (coverage/ambiguity on one side only), so “publisher bias” headlines need care.

### Docs / polish mismatches
- Repository layout mentions expectancy; that metric is **not** implemented.
- Some CLI flags (e.g. leaderboard `min_link_confidence`) do not re-filter already-computed outcomes.
- Stages upsert on re-run; “idempotent” means safe to re-run, not “never rewrite prior rows.”

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev,llm,report]"
cp .env.example .env  # fill API_ID, API_HASH, OPENAI_API_KEY, TWELVEDATA_API_KEY

# Private channel manifest (gitignored). Lists real names locally; never pushed to GitHub.
python scripts/get_groups.py --filter-raw > config/groups.txt
python scripts/get_groups.py --verify

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
streamlit run -m signalyze.report.dashboard
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
  -> market    (Twelve Data, gap-aware        -> market_bars)
  -> evaluate  (walk-forward, first-touch sim -> actual_outcomes)
  -> compare   (reported vs actual            -> discrepancies)
  -> report    (Streamlit + static HTML)
```

See [`docs/architecture.md`](docs/architecture.md) for the per-stage I/O
contract, and [`docs/adr/`](docs/adr/) for design decisions. Prefer this README’s
limitations section over optimistic wording in older docs if they conflict.

## Design principles (intent)

These describe the target shape of the codebase, not a guarantee that every
edge case is handled:

- **Domain-first.** Pydantic v2 models in `signalyze.domain` are pure data;
  storage and parsing depend on domain, never the reverse.
- **Re-runnable stages.** Stages can be re-run; outputs carry version fields
  (`parse_version` / `linker_version` / `computed_version`). Upserts overwrite
  prior rows for the same key.
- **Deterministic-first, LLM-fallback.** Rules emit
  `(payload, confidence, reasons)`. The LLM fires only below
  `parse.llm_escalation_threshold`, and calls are cached on
  `(model, prompt_version, content_hash)`.
- **Confidence fields.** Classifications, parses, and links carry confidence
  scores for filtering and review export — not all headline CLI tables apply
  those filters after outcomes are already written.
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
    analytics/       # win rate, RR, time-to-hit, TP-depth
    report/          # Streamlit dashboard + static HTML export
    cli.py           # Typer entry point
  scripts/           # get_groups.py (regenerate private groups.txt)
  tests/
    unit/
    golden/          # exact-match thresholds enforced in CI
    fixtures/        # hand-labelled JSONL golden sets
  docs/
    architecture.md
    adr/
  data/              # gitignored: raw/, db/, cache/, reports/
```

## Quality gates (CI)

- `ruff` (lint) + `mypy --strict` (typecheck) on `src/signalyze`.
- `pytest` with golden thresholds at **≥0.95** for signal extraction,
  follow-up extraction, and SIGNAL-class precision in classification
  (on the fixed fixture set — not a guarantee on live channel text).
- LLM cache / budget-cap behavior covered by unit tests.

## License

MIT.
