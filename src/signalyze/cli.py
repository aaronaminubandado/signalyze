"""Signalyze CLI entrypoint. One sub-command per pipeline stage."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer

from signalyze import __version__
from signalyze.config import get_settings
from signalyze.storage import open_database
from signalyze.utils.logging import get_logger, setup_logger
from signalyze.utils.time import parse_utc

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

@ingest_app.command("backfill")
def ingest_backfill(
    raw_dir: Path | None = typer.Option(
        None,
        "--raw-dir",
        help="Directory of legacy `<group_id>.csv` files. Defaults to settings.paths.raw_dir.",
    ),
    parquet_dir: Path | None = typer.Option(
        None,
        "--parquet-dir",
        help="If set, write per-group parquet snapshots here.",
    ),
) -> None:
    """Backfill the `messages` table from existing legacy CSV snapshots."""
    from signalyze.ingest import backfill_from_csv_dir

    settings = get_settings()
    logger = get_logger("signalyze.cli.ingest")
    raw_path = settings.resolve(raw_dir) if raw_dir else settings.resolve(settings.paths.raw_dir)
    parquet_path = settings.resolve(parquet_dir) if parquet_dir else None

    db_path = settings.resolve(settings.paths.db_path)
    with open_database(db_path) as db:
        results = backfill_from_csv_dir(db=db, raw_dir=raw_path, parquet_dir=parquet_path)

    total_read = sum(r.rows_read for r in results)
    total_inserted = sum(r.rows_inserted for r in results)
    logger.info(
        "Backfill done: files=%d read=%d inserted=%d",
        len(results),
        total_read,
        total_inserted,
    )
    typer.echo(f"backfill: files={len(results)} read={total_read} inserted={total_inserted}")


@ingest_app.command("fetch")
def ingest_fetch(
    groups_file: Path | None = typer.Option(
        None, "--groups", help="Groups list file. Defaults to config/groups.txt."
    ),
    since: str | None = typer.Option(
        None, "--since", help="UTC ISO start, e.g. 2026-01-17T00:00:00Z."
    ),
    until: str | None = typer.Option(
        None, "--until", help="UTC ISO end, e.g. 2026-04-17T00:00:00Z."
    ),
    parquet_dir: Path | None = typer.Option(
        None, "--parquet-dir", help="If set, also write per-group parquet snapshots."
    ),
) -> None:
    """Fetch fresh Telegram messages into the `messages` table via Telethon."""
    from telethon.sync import TelegramClient

    from signalyze.ingest import fetch_messages_for_groups, parse_groups_file

    settings = get_settings()
    logger = get_logger("signalyze.cli.ingest")

    if not settings.env.api_id or not settings.env.api_hash:
        raise typer.BadParameter("API_ID and API_HASH must be set in .env to use `ingest fetch`.")

    groups_path = settings.resolve(groups_file) if groups_file else settings.resolve(
        settings.paths.groups_file
    )
    targets = parse_groups_file(groups_path)
    if not targets:
        raise typer.BadParameter(f"No groups parsed from {groups_path}")

    date_from = parse_utc(since) if since else parse_utc(settings.ingest.date_from_utc)
    date_to = parse_utc(until) if until else parse_utc(settings.ingest.date_to_utc)
    parquet_path = settings.resolve(parquet_dir) if parquet_dir else None

    session_name = str(settings.resolve(Path(settings.ingest.session_name)))

    logger.info(
        "Fetching %d groups from %s to %s", len(targets), date_from.isoformat(), date_to.isoformat()
    )
    db_path = settings.resolve(settings.paths.db_path)
    with (
        open_database(db_path) as db,
        TelegramClient(session_name, int(settings.env.api_id), settings.env.api_hash) as client,
    ):
        stats = fetch_messages_for_groups(
            client=client,
            db=db,
            group_targets=targets,
            date_from=_as_naive_utc(date_from),
            date_to=_as_naive_utc(date_to),
            parquet_dir=parquet_path,
        )

    resolved = sum(1 for s in stats if s.status == "ok")
    skipped = len(stats) - resolved
    total_inserted = sum(s.inserted_messages for s in stats)
    typer.echo(
        f"fetch: resolved={resolved} skipped={skipped} inserted={total_inserted}"
    )


def _as_naive_utc(value: datetime) -> datetime:
    """Telethon accepts tz-aware datetimes; pass through after ensuring UTC."""
    return value


app.add_typer(ingest_app, name="ingest")


@classify_app.command("run")
def classify_run(
    use_llm: bool = typer.Option(
        True, "--use-llm/--no-llm", help="Enable LLM fallback for uncertain rule decisions."
    ),
    group_id: str | None = typer.Option(
        None, "--group", help="Restrict to one group_id."
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Only classify the first N unclassified messages."
    ),
) -> None:
    """Classify every message in the canonical store."""
    from signalyze.classify import RuleClassifier
    from signalyze.classify.runner import classify_messages
    from signalyze.domain import Message
    from signalyze.llm import get_llm_client

    settings = get_settings()
    logger = get_logger("signalyze.cli.classify")
    db_path = settings.resolve(settings.paths.db_path)

    with open_database(db_path) as db:
        sql = (
            "SELECT m.* FROM messages m "
            "LEFT JOIN message_classifications c ON c.message_uid = m.message_uid "
            "WHERE c.message_uid IS NULL"
        )
        params: list[object] = []
        if group_id is not None:
            sql += " AND m.group_id = ?"
            params.append(group_id)
        sql += " ORDER BY m.timestamp_utc"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        rows = db.conn.execute(sql, params).fetchall()
        messages = [
            Message(
                message_uid=row["message_uid"],
                group_id=row["group_id"],
                message_id=row["message_id"],
                timestamp_utc=row["timestamp_utc"],
                sender_id=row["sender_id"],
                text=row["text"] or "",
                reply_to_msg_id=row["reply_to_msg_id"],
                views=row["views"],
                forwards=row["forwards"],
                reply_count=row["reply_count"],
                ingested_at=row["ingested_at"],
                ingest_method=row["ingest_method"],
            )
            for row in rows
        ]

        rule_classifier = RuleClassifier(settings)
        llm_client = get_llm_client() if use_llm else None
        stats = classify_messages(
            db=db,
            messages=messages,
            rule_classifier=rule_classifier,
            llm_client=llm_client,
            use_llm=use_llm,
        )

    logger.info(
        "Classified %d msgs (rules=%d llm=%d uncertain=%d) -> %s",
        stats.total,
        stats.rules_decisions,
        stats.llm_decisions,
        stats.uncertain,
        stats.by_class,
    )
    typer.echo(
        f"classify: total={stats.total} rules={stats.rules_decisions} llm={stats.llm_decisions} "
        f"uncertain={stats.uncertain} by_class={stats.by_class}"
    )


app.add_typer(classify_app, name="classify")


@parse_app.command("signals")
def parse_signals(
    use_llm: bool = typer.Option(True, "--use-llm/--no-llm"),
    group_id: str | None = typer.Option(None, "--group"),
) -> None:
    """Extract Signal rows from every SIGNAL-classified message."""
    from signalyze.llm import get_llm_client
    from signalyze.parse.signals_runner import extract_signals

    settings = get_settings()
    logger = get_logger("signalyze.cli.parse")
    db_path = settings.resolve(settings.paths.db_path)
    llm = get_llm_client() if use_llm else None

    with open_database(db_path) as db:
        stats = extract_signals(
            db=db,
            llm_client=llm,
            use_llm=use_llm,
            group_id=group_id,
            settings=settings,
        )

    logger.info(
        "Parsed signals: candidates=%d parsed=%d (rules=%d llm=%d) rejected=%d",
        stats.candidates,
        stats.parsed,
        stats.rules_parsed,
        stats.llm_parsed,
        stats.rejected,
    )
    typer.echo(
        f"parse signals: candidates={stats.candidates} parsed={stats.parsed} "
        f"rules={stats.rules_parsed} llm={stats.llm_parsed} rejected={stats.rejected}"
    )


app.add_typer(parse_app, name="parse")
app.add_typer(link_app, name="link")
app.add_typer(evaluate_app, name="evaluate")
app.add_typer(market_app, name="market")
app.add_typer(compare_app, name="compare")
app.add_typer(report_app, name="report")


if __name__ == "__main__":
    app()
