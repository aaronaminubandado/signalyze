# Signalyze

A modular pipeline that turns Telegram trading-signal channels into auditable,
reproducible performance analytics — including a reported-vs-actual comparison
layer that exposes systematic publisher bias.

> **v1 scope:** XAUUSD, batch/historical analysis. Multi-instrument, live
> ingestion, and tick-resolution simulation are tracked as follow-on work.

This is the scaffold. Subsequent commits add each pipeline stage as its own
module under `src/signalyze/` with its own tests.

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev,llm,report]"
cp .env.example .env
cp config/groups.example.txt config/groups.txt  # then edit with your channels (gitignored)

signalyze init-db
```

## Repository layout

```
signalyze/
  config/            # settings.toml + groups.example.txt (real groups.txt is gitignored)
  src/signalyze/     # package
    domain/          # pydantic v2 models (zero I/O)
    storage/         # SQLite + Parquet + LLM cache, with SQL migrations
    utils/           # time (UTC), money (pips), logging
    llm/             # provider-agnostic client + cost cap + cache
    cli.py           # Typer entry point (stages registered incrementally)
  tests/
    unit/            # unit tests
  data/              # gitignored: raw/, db/, cache/, reports/
```

## License

MIT.
