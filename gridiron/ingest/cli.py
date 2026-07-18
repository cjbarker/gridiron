"""Command-line entry point for ingestion.

Examples::

    # Live CFBD backfill (needs CFBD_API_KEY), one season:
    python -m gridiron.ingest.cli --year 2023

    # A range of seasons:
    python -m gridiron.ingest.cli --start 2014 --end 2024

    # From on-disk JSON fixtures (no network/key):
    python -m gridiron.ingest.cli --year 2023 --fixtures tests/fixtures/season2023
"""

from __future__ import annotations

import argparse
import sys

from gridiron.db.init_db import create_all
from gridiron.ingest.pipeline import ingest_season
from gridiron.ingest.sources import CFBDSource, DataSource, FixtureSource


def _build_source(args: argparse.Namespace) -> DataSource:
    if args.fixtures:
        return FixtureSource(args.fixtures)
    return CFBDSource(api_key=args.api_key)


def _years(args: argparse.Namespace) -> list[int]:
    if args.year is not None:
        return [args.year]
    if args.start is not None and args.end is not None:
        return list(range(args.start, args.end + 1))
    raise SystemExit("Specify --year, or both --start and --end.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest college football season data.")
    parser.add_argument("--year", type=int, help="Single season to ingest.")
    parser.add_argument("--start", type=int, help="First season (inclusive) of a range.")
    parser.add_argument("--end", type=int, help="Last season (inclusive) of a range.")
    parser.add_argument("--fixtures", help="Path to a JSON fixture directory (offline source).")
    parser.add_argument("--api-key", help="CFBD API key (else read from CFBD_API_KEY).")
    parser.add_argument(
        "--no-stats", action="store_true", help="Skip player/team box-score ingestion."
    )
    parser.add_argument(
        "--no-rosters", action="store_true", help="Skip roster (players table) ingestion."
    )
    parser.add_argument(
        "--init-db", action="store_true", help="Create tables before ingesting."
    )
    args = parser.parse_args(argv)

    if args.init_db:
        create_all()

    source = _build_source(args)
    for year in _years(args):
        report = ingest_season(
            source, year, with_stats=not args.no_stats, with_rosters=not args.no_rosters
        )
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
