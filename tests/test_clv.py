"""Tests for closing line value (CLV) — opening vs closing line movement."""

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


def test_game_line_movement(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        mv = q.game_line_movement(s, 401520281)
    row = mv[0]
    assert row["spread_open"] == -3.5 and row["spread_close"] == -2.5
    assert row["spread_move"] == 1.0  # home became less favored
    assert row["total_move"] == pytest.approx(-2.0)  # 52.5 - 54.5


def test_team_clv(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        ga = q.team_clv(s, "Georgia", 2023)
        al = q.team_clv(s, "Alabama", 2023)
    # Georgia: game1 open -3.5 -> close -2.5 => CLV = -3.5-(-2.5) = -1.0 (moved away);
    # game2 open -35.5 -> close -38.5 => CLV = -35.5-(-38.5) = +3.0 (moved toward).
    assert ga["games_with_movement"] == 2
    assert ga["avg_clv_points"] == 1.0  # (-1.0 + 3.0) / 2
    assert ga["beat_close_rate"] == 0.5
    # Alabama away in game1: sign flips -> CLV = +1.0 (market moved toward Bama).
    assert al["avg_clv_points"] == 1.0


def test_clv_leaders(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        leaders = q.clv_leaders(s, 2023)
    teams = {r["team"]: r for r in leaders}
    assert "Georgia" in teams and "Alabama" in teams
    # Ball State only played a game with movement toward Georgia (against Ball State),
    # so Ball State's CLV backing them is negative.
    assert teams["Ball State"]["avg_clv_points"] < 0


def test_clv_endpoints_and_pages(client):
    mv = client.get("/api/games/401520281/line-movement")
    assert mv.status_code == 200 and mv.json()[0]["spread_move"] == 1.0
    ldr = client.get("/api/analytics/clv-leaders", params={"season": 2023})
    assert ldr.status_code == 200 and any(r["team"] == "Georgia" for r in ldr.json())
    clv = client.get("/api/teams/Georgia/clv", params={"season": 2023})
    assert clv.status_code == 200 and clv.json()["avg_clv_points"] == 1.0
    # Game page shows open→close; team page shows a CLV pill; leaders page has the chart.
    assert "open→close" in client.get("/games/401520281").text
    assert "CLV 1.0 pts" in client.get("/teams/Georgia", params={"season": 2023}).text
    assert "chart-clv" in client.get("/leaders", params={"season": 2023}).text
