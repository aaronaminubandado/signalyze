"""Signalyze CLI entrypoint. One sub-command per pipeline stage."""

from __future__ import annotations

import typer

from signalyze import __version__
from signalyze.config import get_settings
from signalyze.storage import open_database
from signalyze.utils.logging import setup_logger

app = typer.Typer(
    add_completion=False,
    help="Telegram trading-signal collection, follow-up linking, and reported-vs-actual analytics.",
)


@app.callback()
def _root(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    setup_logger(level=10 if verbose else 20)
    ctx.ensure_object(dict)


@app.command()
def version() -> None:
    """Print the Signalyze package version."""
    typer.echo(__version__)


@app.command("init-db")
def init_db() -> None:
    """Create the canonical SQLite database and apply migrations."""
    settings = get_settings()
    path = settings.resolve(settings.paths.db_path)
    with open_database(path):
        pass
    typer.echo(f"Initialized database at {path}")


# Sub-applications for each stage. Implementations are added incrementally per phase.
ingest_app = typer.Typer(help="Stage 1: ingest Telegram messages or backfill existing CSVs.")
classify_app = typer.Typer(help="Stage 2: classify messages as SIGNAL / FOLLOW_UP / NOISE.")
parse_app = typer.Typer(help="Stages 3-4: parse signals and follow-up events.")
link_app = typer.Typer(help="Stage 5: link follow-ups to their parent signals.")
evaluate_app = typer.Typer(help="Stages 6 & 8: compute reported and actual outcomes.")
market_app = typer.Typer(help="Stage 7: fetch market bars from configured provider.")
compare_app = typer.Typer(help="Stage 9: compare reported vs actual outcomes and analytics.")
report_app = typer.Typer(help="Stage 10: reports + dashboard.")

app.add_typer(ingest_app, name="ingest")
app.add_typer(classify_app, name="classify")
app.add_typer(parse_app, name="parse")
app.add_typer(link_app, name="link")
app.add_typer(evaluate_app, name="evaluate")
app.add_typer(market_app, name="market")
app.add_typer(compare_app, name="compare")
app.add_typer(report_app, name="report")


if __name__ == "__main__":
    app()
