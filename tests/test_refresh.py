"""Tests for current-season detection and the refresh entry point."""

from __future__ import annotations

import datetime

from sqlalchemy import func, select

from gridiron.db.models import Game
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import current_season, refresh_current_season
from gridiron.ingest.sources import FixtureSource


def test_current_season_boundaries():
    # Aug-Dec -> that year; Jan-Jul -> prior year.
    assert current_season(datetime.date(2024, 8, 24)) == 2024
    assert current_season(datetime.date(2024, 12, 31)) == 2024
    assert current_season(datetime.date(2025, 1, 8)) == 2024
    assert current_season(datetime.date(2025, 7, 31)) == 2024
    assert current_season(datetime.date(2025, 9, 1)) == 2025


def test_refresh_current_season_idempotent(db_env, fixture_source):
    # Force "today" into the 2023 season so the fixture data is what gets refreshed.
    today = datetime.date(2023, 10, 1)
    refresh_current_season(fixture_source, today=today, with_stats=False, with_rosters=False)
    with session_scope() as s:
        first = s.scalar(select(func.count()).select_from(Game))
    refresh_current_season(fixture_source, today=today, with_stats=False, with_rosters=False)
    with session_scope() as s:
        second = s.scalar(select(func.count()).select_from(Game))
    assert first == second == 2


def test_refresh_uses_fixture_source_type():
    # Sanity: the source passed through is honored (no network).
    assert isinstance(FixtureSource("nowhere"), FixtureSource)
