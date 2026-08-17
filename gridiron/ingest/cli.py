"""Command-line entry point for ingestion.

Examples::

    # Live CFBD backfill (needs CFBD_API_KEY), one season:
    uv run gridiron-ingest --year 2023

    # A range of seasons:
    uv run gridiron-ingest --start 2014 --end 2024

    # Bulk historical backfill: plays (with EPA/WP) from the cfbfastR parquet,
    # games/drives/rankings from the CFBD API:
    uv run gridiron-ingest --start 2002 --end 2024 --plays-source parquet

    # Fully offline backfill (no CFBD key): synthesize games from the parquet:
    uv run gridiron-ingest --start 2014 --end 2024 \
        --plays-source parquet --stub-games

    # From on-disk JSON fixtures (no network/key):
    uv run gridiron-ingest --year 2023 --fixtures tests/fixtures/season2023

    # Inspect a parquet's columns without writing to the DB:
    uv run gridiron-ingest --year 2023 --inspect-parquet
"""

from __future__ import annotations

import argparse
import functools
import sys

from gridiron.db.init_db import create_all
from gridiron.ingest.pipeline import current_season, ingest_season
from gridiron.ingest.progress import ProgressReporter
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
    source = getattr(args, "source", "cfbd")
    if source in ("espn", "hybrid"):
        # Imported lazily so the ESPN path has no import cost for CFBD-only runs.
        from gridiron.ingest.espn import ESPNSource, HybridSource

        core = ESPNSource(years=getattr(args, "_years", None))
        if source == "espn":
            return core
        # CFBD (for the four extras) is built lazily — no key needed unless used.
        return HybridSource(core=core, extras_factory=lambda: CFBDSource(api_key=args.api_key))
    # Offline parquet backfill: no API source needed when synthesizing games.
    if args.plays_source == "parquet" and args.stub_games and not args.api_key:
        return EmptySource()
    return CFBDSource(api_key=args.api_key)


def _years(args: argparse.Namespace) -> list[int]:
    if getattr(args, "refresh", False):
        return [current_season()]
    if args.year is not None:
        return [args.year]
    if args.start is not None and args.end is not None:
        return list(range(args.start, args.end + 1))
    raise SystemExit("Specify --year, --start/--end, or --refresh.")


def _inspect_parquet(years: list[int], base_url: str | None) -> int:
    for year in years:
        rows = load_pbp_parquet(year, base_url)
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
        "--no-lines", action="store_true", help="Skip betting-line ingestion."
    )
    parser.add_argument(
        "--no-recruiting", action="store_true", help="Skip recruiting/transfer ingestion."
    )
    parser.add_argument(
        "--no-coaches", action="store_true", help="Skip coaching-record ingestion."
    )
    parser.add_argument(
        "--init-db", action="store_true", help="Create tables before ingesting."
    )
    parser.add_argument(
        "--source",
        choices=("cfbd", "espn", "hybrid"),
        default="cfbd",
        help="Data source: CFBD (default, needs key), ESPN (key-free core), or hybrid "
        "(ESPN core + CFBD for recruiting/transfers/coaches/lines).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Idempotently (re-)ingest the current season (for cron). Overrides --year.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the live progress bar (auto-off already when not a TTY).",
    )
    args = parser.parse_args(argv)

    if args.inspect_parquet:
        return _inspect_parquet(_years(args), args.parquet_base_url)

    if args.init_db:
        create_all()

    loader = functools.partial(load_pbp_parquet, base_url=args.parquet_base_url)
    years = _years(args)
    args._years = years  # lets an ESPN source pre-populate venues for these seasons
    source = _build_source(args)
    reporter = ProgressReporter(years, enabled=False if args.quiet else None)
    for year in years:
        report = ingest_season(
            source,
            year,
            with_stats=not args.no_stats,
            with_rosters=not args.no_rosters,
            with_lines=not args.no_lines,
            with_recruiting=not args.no_recruiting,
            with_coaches=not args.no_coaches,
            plays_source=args.plays_source,
            parquet_loader=loader if args.plays_source == "parquet" else None,
            stub_games=args.stub_games,
            progress=reporter,
        )
        # Clears the live bar (if drawn) and prints the season's final counts.
        reporter.finish_year(str(report))
    return 0


def refresh_main(argv: list[str] | None = None) -> int:
    """Entry point for the ``gridiron-refresh`` console script (implies --refresh)."""
    return main(["--refresh", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    sys.exit(main())
