"""Tests for win-probability flow and leaderboard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gridiron.analytics import queries as q
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


@pytest.fixture()
def client(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    from gridiron.api.main import app

    return TestClient(app)


def test_game_win_probability_home_perspective(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = q.game_win_probability(s, 401520281)
    # 5 plays in game1 all have wp. Georgia is home.
    assert len(rows) == 5
    by_offense = rows
    # Georgia offensive play wp stays as-is (0.55); Alabama play (wp 0.48) flips to 0.52.
    ga_first = next(r for r in by_offense if r["offense"] == "Georgia")
    al_play = next(r for r in by_offense if r["offense"] == "Alabama")
    assert ga_first["home_wp"] == 0.55
    assert al_play["home_wp"] == round(1 - 0.48, 4)  # 0.52


def test_wp_leaders(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["team"]: r for r in q.wp_leaders(s, 2023, min_plays=1)}
    # Georgia's offensive wp values average above Alabama's.
    assert rows["Georgia"]["avg_wp"] > rows["Alabama"]["avg_wp"]


def test_wp_endpoints_and_pages(client):
    wp = client.get("/api/games/401520281/win-probability")
    assert wp.status_code == 200 and len(wp.json()) == 5
    leaders = client.get("/api/analytics/wp-leaders", params={"season": 2023})
    assert leaders.status_code == 200 and any(r["team"] == "Georgia" for r in leaders.json())
    # Game page shows the WP chart; leaders page renders.
    assert "chart-wp" in client.get("/games/401520281").text
    assert client.get("/leaders", params={"season": 2023}).status_code == 200
