#!/usr/bin/env python3
"""List Telegram groups/channels in groups.txt format (for the gitignored manifest).

Usage:
    python scripts/get_groups.py > config/groups.txt
    python scripts/get_groups.py --filter-raw   # only channels with data/raw/<id>.csv
    python scripts/get_groups.py --verify     # check coverage vs data/raw/*.csv

Requires API_ID and API_HASH in .env (same as signalyze ingest fetch).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _format_username(username: str | None) -> str:
    if not username:
        return "None"
    return username.lstrip("@")


def _iter_dialog_lines(*, filter_raw: bool) -> list[str]:
    from telethon.sync import TelegramClient

    from signalyze.config import get_settings

    settings = get_settings()
    api_id = settings.env.api_id
    api_hash = settings.env.api_hash
    if not api_id or not api_hash:
        raise SystemExit(
            "API_ID and API_HASH must be set in .env. "
            "Get them from https://my.telegram.org/apps"
        )

    raw_ids: set[str] | None = None
    if filter_raw:
        raw_dir = settings.resolve(settings.paths.raw_dir)
        raw_ids = {p.stem for p in raw_dir.glob("*.csv")}
        if not raw_ids:
            raise SystemExit(f"No CSV files found under {raw_dir}")

    session_name = str(settings.resolve(Path(settings.ingest.session_name)))
    lines: list[str] = []

    with TelegramClient(session_name, int(api_id), api_hash) as client:
        for dialog in client.iter_dialogs():
            if not (dialog.is_group or dialog.is_channel):
                continue
            group_id = str(dialog.id)
            if raw_ids is not None and group_id not in raw_ids:
                continue
            username = _format_username(getattr(dialog.entity, "username", None))
            name = (dialog.name or group_id).replace("\n", " ").strip()
            lines.append(f"{name:40} | id: {group_id} | username: {username}")

    return sorted(lines, key=lambda line: line.lower())


def _verify_coverage(groups_file: Path, raw_dir: Path) -> int:
    from signalyze.ingest.groups_loader import build_label_map

    if not groups_file.exists():
        print(f"Missing {groups_file}", file=sys.stderr)
        print("Run: python scripts/get_groups.py > config/groups.txt", file=sys.stderr)
        return 1

    label_map = build_label_map(groups_file)
    if not label_map:
        print(f"{groups_file} has no parseable id: lines", file=sys.stderr)
        return 1

    raw_ids = sorted(p.stem for p in raw_dir.glob("*.csv"))
    if not raw_ids:
        print(f"No CSV files under {raw_dir}", file=sys.stderr)
        return 0

    missing = [gid for gid in raw_ids if gid not in label_map]
    extra = [gid for gid in label_map if gid not in set(raw_ids)]

    print(f"groups.txt: {len(label_map)} ids, data/raw: {len(raw_ids)} csv files")
    if missing:
        print(f"Missing from groups.txt ({len(missing)}):", file=sys.stderr)
        for gid in missing:
            print(f"  {gid}", file=sys.stderr)
    if extra:
        print(f"In groups.txt but no raw CSV ({len(extra)}):", file=sys.stderr)
        for gid in extra[:10]:
            print(f"  {gid}", file=sys.stderr)
        if len(extra) > 10:
            print(f"  ... and {len(extra) - 10} more", file=sys.stderr)

    if missing:
        return 1
    print("All raw CSV group ids are covered in groups.txt.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--filter-raw",
        action="store_true",
        help="Only print groups that have a matching data/raw/<id>.csv file.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify config/groups.txt covers every data/raw/*.csv stem; do not fetch.",
    )
    parser.add_argument(
        "--groups-file",
        type=Path,
        default=Path("config/groups.txt"),
        help="Manifest path (default: config/groups.txt).",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=None,
        help="Raw CSV directory for --verify / --filter-raw (default: settings.paths.raw_dir).",
    )
    args = parser.parse_args()

    from signalyze.config import get_settings

    settings = get_settings()
    groups_file = settings.resolve(args.groups_file)
    raw_dir = settings.resolve(args.raw_dir) if args.raw_dir else settings.resolve(settings.paths.raw_dir)

    if args.verify:
        return _verify_coverage(groups_file, raw_dir)

    lines = _iter_dialog_lines(filter_raw=args.filter_raw)
    if not lines:
        print("No matching groups/channels found.", file=sys.stderr)
        return 1

    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
