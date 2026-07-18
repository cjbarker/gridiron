"""Command-line entry point for ingestion.

Examples::

    # Live CFBD backfill (needs CFBD_API_KEY), one season:
    python -m gridiron.ingest.cli --year 2023

    # A range of seasons:
    python -m gridiron.ingest.cli --start 2014 --end 2024

    # Bulk historical backfill: plays (with EPA/WP) from the cfbfastR parquet,
    # games/drives/rankings from the CFBD API:
    python -m gridiron.ingest.cli --start 2002 --end 2024 --plays-source parquet

    # Fully offline backfill (no CFBD key): synthesize games from the parquet:
    python -m gridiron.ingest.cli --start 2014 --end 2024 \
        --plays-source parquet --stub-games

    # From on-disk JSON fixtures (no network/key):
    python -m gridiron.ingest.cli --year 2023 --fixtures tests/fixtures/season2023

    # Inspect a parquet's columns without writing to the DB:
    python -m gridiron.ingest.cli --year 2023 --inspect-parquet
"""

from __future__ import annotations

import argparse
import sys

from gridiron.db.init_db import create_all
from gridiron.ingest.pipeline import ingest_season
from gridiron.ingest.sources import (
    CFBDSource,
    DataSource,
    EmptySource,
    FixtureSource,
    load_pbp_parquet,
)


def _build_source(args: argparse.Namespace) -> DataSource:
    if args.fixtures:
        return FixtureSource(args.fixtures)
    # Offline parquet backfill: no API source needed when synthesizing games.
    if args.plays_source == "parquet" and args.stub_games and not args.api_key:
        return EmptySource()
    return CFBDSource(api_key=args.api_key)


def _years(args: argparse.Namespace) -> list[int]:
    if args.year is not None:
        return [args.year]
    if args.start is not None and args.end is not None:
        return list(range(args.start, args.end + 1))
    raise SystemExit("Specify --year, or both --start and --end.")


def _inspect_parquet(years: list[int], base_url: str | None) -> int:
    for year in years:
        rows = load_pbp_parquet(year, base_url) if base_url else load_pbp_parquet(year)
        cols = sorted({k for r in rows[:200] for k in r}) if rows else []
        print(f"[{year}] rows={len(rows)} columns={len(cols)}")
        print("  " + ", ".join(cols))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest college football season data.")
    parser.add_argument("--year", type=int, help="Single season to ingest.")
    parser.add_argument("--start", type=int, help="First season (inclusive) of a range.")
    parser.add_argument("--end", type=int, help="Last season (inclusive) of a range.")
    parser.add_argument("--fixtures", help="Path to a JSON fixture directory (offline source).")
    parser.add_argument("--api-key", help="CFBD API key (else read from CFBD_API_KEY).")
    parser.add_argument(
        "--plays-source",
        choices=("api", "parquet"),
        default="api",
        help="Where plays come from: CFBD API (default) or cfbfastR bulk parquet (EPA/WP).",
    )
    parser.add_argument(
        "--parquet-base-url", help="Override the base URL for parquet play-by-play files."
    )
    parser.add_argument(
        "--stub-games",
        action="store_true",
        help="With --plays-source parquet, synthesize missing games from the parquet (offline).",
    )
    parser.add_argument(
        "--inspect-parquet",
        action="store_true",
        help="Print the parquet's row count + columns for each year and exit (no DB writes).",
    )
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

    if args.inspect_parquet:
        return _inspect_parquet(_years(args), args.parquet_base_url)

    if args.init_db:
        create_all()

    def loader(year: int) -> list[dict]:
        if args.parquet_base_url:
            return load_pbp_parquet(year, args.parquet_base_url)
        return load_pbp_parquet(year)

    source = _build_source(args)
    for year in _years(args):
        report = ingest_season(
            source,
            year,
            with_stats=not args.no_stats,
            with_rosters=not args.no_rosters,
            plays_source=args.plays_source,
            parquet_loader=loader if args.plays_source == "parquet" else None,
            stub_games=args.stub_games,
        )
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
