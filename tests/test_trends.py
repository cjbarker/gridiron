"""Tests for season-over-season team trends."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gridiron.analytics import queries as q
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season
from gridiron.ingest.sources import FixtureSource

FIX_2022 = Path(__file__).parent / "fixtures" / "season2022"


@pytest.fixture()
def two_seasons(db_env, fixture_source):
    # 2023 (full fixtures) + 2022 (games-only) both feature Georgia.
    ingest_season(fixture_source, 2023)
    ingest_season(FixtureSource(FIX_2022), 2022)


def test_team_season_history(two_seasons):
    with session_scope() as s:
        hist = q.team_season_history(s, "Georgia")
    seasons = [h["season"] for h in hist]
    assert seasons == [2022, 2023]  # ordered ascending
    by_season = {h["season"]: h for h in hist}
    assert by_season[2022]["ppg"] == 49.0  # single 49-3 game
    assert by_season[2023]["ppg"] == 36.0  # 27 and 45


def test_trends_endpoint_and_page(two_seasons):
    from gridiron.api.main import app

    client = TestClient(app)
    trends = client.get("/api/teams/Georgia/trends")
    assert trends.status_code == 200
    assert [h["season"] for h in trends.json()] == [2022, 2023]
    # Team page shows the season-over-season chart when >1 season exists.
    page = client.get("/teams/Georgia", params={"season": 2023})
    assert page.status_code == 200 and "chart-trends" in page.text


def test_single_season_no_trends_chart(db_env, fixture_source):
    ingest_season(fixture_source, 2023)  # only 2023
    from gridiron.api.main import app

    page = TestClient(app).get("/teams/Georgia", params={"season": 2023})
    assert page.status_code == 200 and "chart-trends" not in page.text
