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


@parse_app.command("follow-ups")
def parse_follow_ups(
    use_llm: bool = typer.Option(True, "--use-llm/--no-llm"),
    group_id: str | None = typer.Option(None, "--group"),
) -> None:
    """Extract FollowUpEvent rows from every FOLLOW_UP-classified message."""
    from signalyze.llm import get_llm_client
    from signalyze.parse.follow_ups_runner import extract_follow_ups

    settings = get_settings()
    logger = get_logger("signalyze.cli.parse")
    db_path = settings.resolve(settings.paths.db_path)
    llm = get_llm_client() if use_llm else None

    with open_database(db_path) as db:
        stats = extract_follow_ups(
            db=db,
            llm_client=llm,
            use_llm=use_llm,
            group_id=group_id,
            settings=settings,
        )

    logger.info(
        "Parsed follow-ups: candidates=%d parsed=%d (rules=%d llm=%d) rejected=%d",
        stats.candidates,
        stats.parsed,
        stats.rules_parsed,
        stats.llm_parsed,
        stats.rejected,
    )
    typer.echo(
        f"parse follow-ups: candidates={stats.candidates} parsed={stats.parsed} "
        f"rules={stats.rules_parsed} llm={stats.llm_parsed} rejected={stats.rejected}"
    )


app.add_typer(parse_app, name="parse")


@link_app.command("run")
def link_run(
    group_id: str | None = typer.Option(None, "--group"),
    use_llm: bool = typer.Option(True, "--use-llm/--no-llm"),
) -> None:
    """Link follow-ups to their parent signals."""
    from signalyze.link import Linker
    from signalyze.llm import get_llm_client

    settings = get_settings()
    logger = get_logger("signalyze.cli.link")
    db_path = settings.resolve(settings.paths.db_path)
    llm = get_llm_client() if use_llm else None
    linker = Linker(settings=settings, llm_client=llm)

    with open_database(db_path) as db:
        stats = linker.run(db, group_id=group_id)

    logger.info(
        "Linker: follow_ups=%d linked=%d unlinked=%d low_conf=%d by_method=%s",
        stats.follow_ups,
        stats.linked,
        stats.unlinked,
        stats.low_confidence,
        stats.by_method,
    )
    typer.echo(
        f"link: follow_ups={stats.follow_ups} linked={stats.linked} "
        f"unlinked={stats.unlinked} low_conf={stats.low_confidence} by_method={stats.by_method}"
    )


@link_app.command("export-review")
def link_export_review(
    output: Path = typer.Option(
        Path("data/reports/links_low_confidence.csv"), "--output", "-o",
    ),
    threshold: float = typer.Option(0.6, "--threshold"),
) -> None:
    """Export low-confidence links to a CSV for manual review."""
    from signalyze.link.linker import export_low_confidence_csv

    settings = get_settings()
    db_path = settings.resolve(settings.paths.db_path)
    output_path = settings.resolve(output)
    with open_database(db_path) as db:
        count = export_low_confidence_csv(db, output_path, threshold=threshold)
    typer.echo(f"export-review: {count} links written to {output_path}")


app.add_typer(link_app, name="link")


@evaluate_app.command("reported")
def evaluate_reported(
    min_link_confidence: float = typer.Option(0.6, "--min-link-confidence"),
    group_id: str | None = typer.Option(None, "--group"),
) -> None:
    """Compute reported outcomes from linked follow-ups."""
    from signalyze.evaluate import compute_reported_outcomes

    settings = get_settings()
    logger = get_logger("signalyze.cli.evaluate")
    db_path = settings.resolve(settings.paths.db_path)
    with open_database(db_path) as db:
        stats = compute_reported_outcomes(
            db=db,
            min_link_confidence=min_link_confidence,
            settings=settings,
            group_id=group_id,
        )
    logger.info(
        "Reported outcomes: signals=%d written=%d by_state=%s",
        stats.signals,
        stats.outcomes_written,
        stats.by_state,
    )
    typer.echo(
        f"evaluate reported: signals={stats.signals} written={stats.outcomes_written} "
        f"by_state={stats.by_state}"
    )


@evaluate_app.command("actual")
def evaluate_actual(
    group_id: str | None = typer.Option(None, "--group"),
    max_holding_hours: float | None = typer.Option(None, "--max-holding-hours"),
    default_sl_policy: str | None = typer.Option(None, "--default-sl-policy"),
    win_policy: str | None = typer.Option(None, "--win-policy"),
) -> None:
    """Walk-forward simulate every signal against cached market bars."""
    from signalyze.evaluate import SimulationConfig, simulate_all

    settings = get_settings()
    logger = get_logger("signalyze.cli.evaluate")
    db_path = settings.resolve(settings.paths.db_path)

    config: SimulationConfig | None = None
    if any(v is not None for v in (max_holding_hours, default_sl_policy, win_policy)):
        from signalyze.domain import WinPolicy

        base = SimulationConfig(
            win_policy=WinPolicy(settings.evaluate.win_policy),
            max_holding_hours=settings.evaluate.max_holding_hours,
            default_sl_policy=settings.evaluate.default_sl_policy,
            default_sl_pips=settings.evaluate.default_sl_pips,
        )
        config = SimulationConfig(
            win_policy=WinPolicy(win_policy) if win_policy else base.win_policy,
            max_holding_hours=max_holding_hours
            if max_holding_hours is not None
            else base.max_holding_hours,
            default_sl_policy=default_sl_policy or base.default_sl_policy,
            default_sl_pips=base.default_sl_pips,
        )

    with open_database(db_path) as db:
        stats = simulate_all(db=db, settings=settings, group_id=group_id, config=config)

    logger.info(
        "Actual outcomes: signals=%d written=%d insufficient=%d by_state=%s",
        stats.signals,
        stats.outcomes_written,
        stats.skipped_insufficient_data,
        stats.by_state,
    )
    typer.echo(
        f"evaluate actual: signals={stats.signals} written={stats.outcomes_written} "
        f"insufficient={stats.skipped_insufficient_data} by_state={stats.by_state}"
    )


@evaluate_app.command("leaderboard")
def evaluate_leaderboard(
    min_link_confidence: float = typer.Option(0.6, "--min-link-confidence"),
) -> None:
    """Print a reported-win-rate leaderboard per group."""
    settings = get_settings()
    db_path = settings.resolve(settings.paths.db_path)
    with open_database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT s.group_id,
                   COUNT(*) AS n_signals,
                   SUM(CASE WHEN r.final_state = 'WIN' THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN r.final_state = 'LOSS' THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN r.final_state = 'OPEN' THEN 1 ELSE 0 END) AS open_,
                   SUM(CASE WHEN r.final_state = 'NO_REPORT' THEN 1 ELSE 0 END) AS no_report
            FROM signals s
            JOIN reported_outcomes r ON r.signal_id = s.signal_id
            GROUP BY s.group_id
            ORDER BY wins DESC
            """
        ).fetchall()

    typer.echo(
        f"{'group_id':<20} {'n':>5} {'wins':>5} {'losses':>7} "
        f"{'open':>5} {'no_rep':>7} {'win%':>6}"
    )
    for row in rows:
        n = row["n_signals"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        decided = wins + losses
        win_rate = (wins / decided * 100.0) if decided else 0.0
        typer.echo(
            f"{row['group_id']:<20} {n:>5} {wins:>5} {losses:>7} {row['open_'] or 0:>5} "
            f"{row['no_report'] or 0:>7} {win_rate:>5.1f}%"
        )


app.add_typer(evaluate_app, name="evaluate")


@market_app.command("fetch")
def market_fetch(
    instrument: str = typer.Option("XAUUSD", "--instrument"),
    interval: str = typer.Option("1min", "--interval"),
    provider: str = typer.Option("auto", "--provider", help="twelvedata | csv | auto"),
    csv_path: Path | None = typer.Option(None, "--csv-path"),
) -> None:
    """Fetch the market bars required to evaluate every signal in the DB."""
    from signalyze.market import MarketDataProvider, fetch_required_bars
    from signalyze.market.providers import CSVProvider, TwelveDataProvider

    settings = get_settings()
    logger = get_logger("signalyze.cli.market")
    db_path = settings.resolve(settings.paths.db_path)

    chosen = provider
    if chosen == "auto":
        chosen = "csv" if csv_path is not None else settings.env.market_provider or "twelvedata"

    market_provider: MarketDataProvider
    if chosen == "csv":
        if csv_path is None:
            raise typer.BadParameter("--csv-path is required with --provider=csv")
        market_provider = CSVProvider(settings.resolve(csv_path))
    elif chosen == "twelvedata":
        api_key = settings.env.twelvedata_api_key or ""
        if not api_key:
            raise typer.BadParameter("TWELVEDATA_API_KEY not set in environment")
        market_provider = TwelveDataProvider(api_key=api_key)
    else:
        raise typer.BadParameter(f"unknown provider: {chosen}")

    with open_database(db_path) as db:
        stats = fetch_required_bars(
            db=db,
            provider=market_provider,
            instrument=instrument,
            interval=interval,
            settings=settings,
        )

    logger.info(
        "market fetch: provider=%s instrument=%s interval=%s cached=%d fetched=%d segments=%d errors=%d",
        chosen,
        instrument,
        interval,
        stats.cached_bars,
        stats.fetched_bars,
        stats.requested_segments,
        len(stats.errors),
    )
    typer.echo(
        f"market fetch: provider={chosen} cached={stats.cached_bars} fetched={stats.fetched_bars} "
        f"segments={stats.requested_segments} errors={len(stats.errors)}"
    )
    for err in stats.errors[:5]:
        typer.echo(f"  ! {err}")


app.add_typer(market_app, name="market")


@compare_app.command("run")
def compare_run(
    group_id: str | None = typer.Option(None, "--group"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write per-signal CSV."),
) -> None:
    """Compare reported vs actual outcomes per signal and summarize categories."""
    from signalyze.compare import compute_discrepancies

    settings = get_settings()
    db_path = settings.resolve(settings.paths.db_path)
    with open_database(db_path) as db:
        rows = compute_discrepancies(db=db, group_id=group_id)

    by_category: dict[str, int] = {}
    for row in rows:
        by_category[row.category.value] = by_category.get(row.category.value, 0) + 1

    typer.echo(f"compare: signals={len(rows)} categories={by_category}")

    if output is not None:
        import csv as _csv

        output_path = settings.resolve(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = _csv.writer(handle)
            writer.writerow(
                [
                    "signal_id",
                    "group_id",
                    "reported_state",
                    "actual_state",
                    "category",
                    "reported_pips",
                    "actual_pips",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row.signal_id,
                        row.group_id,
                        row.reported_state.value,
                        row.actual_state.value,
                        row.category.value,
                        row.reported_pips,
                        row.actual_pips,
                    ]
                )
        typer.echo(f"compare: wrote {len(rows)} rows to {output_path}")


@compare_app.command("metrics")
def compare_metrics(
    start_utc: str | None = typer.Option(None, "--start"),
    end_utc: str | None = typer.Option(None, "--end"),
) -> None:
    """Print per-group reported-vs-actual analytics table."""
    from signalyze.analytics import iter_group_metrics

    settings = get_settings()
    db_path = settings.resolve(settings.paths.db_path)
    with open_database(db_path) as db:
        rows = sorted(
            iter_group_metrics(db=db, start_utc=start_utc, end_utc=end_utc),
            key=lambda m: -(m.actual_win_rate or 0.0),
        )

    header = (
        f"{'group_id':<20} {'n':>4} {'rep_w%':>7} {'act_w%':>7} {'gap':>6} "
        f"{'avg_pips':>9} {'avg_rr':>7} {'amb':>4} {'no_data':>8}"
    )
    typer.echo(header)
    typer.echo("-" * len(header))
    for m in rows:
        rep = f"{(m.reported_win_rate or 0) * 100:6.1f}%" if m.reported_win_rate is not None else "    n/a"
        act = f"{(m.actual_win_rate or 0) * 100:6.1f}%" if m.actual_win_rate is not None else "    n/a"
        gap = f"{(m.win_rate_gap or 0) * 100:+5.1f}" if m.win_rate_gap is not None else "  n/a"
        pips = f"{m.avg_realized_pips:8.1f}" if m.avg_realized_pips is not None else "     n/a"
        rr = f"{m.avg_realized_rr:6.2f}" if m.avg_realized_rr is not None else "   n/a"
        typer.echo(
            f"{m.group_id:<20} {m.n_signals:>4} {rep:>7} {act:>7} {gap:>6} "
            f"{pips:>9} {rr:>7} {m.ambiguous_bars:>4} {m.insufficient_data:>8}"
        )


app.add_typer(compare_app, name="compare")
app.add_typer(report_app, name="report")


if __name__ == "__main__":
    app()
