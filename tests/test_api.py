"""Smoke tests for the FastAPI JSON endpoints and HTML pages."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gridiron.ingest.pipeline import ingest_season


@pytest.fixture()
def client(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    from gridiron.api.main import app

    return TestClient(app)


def test_teams_endpoint(client):
    resp = client.get("/api/teams")
    assert resp.status_code == 200
    schools = {t["school"] for t in resp.json()}
    assert {"Georgia", "Alabama"} <= schools


def test_games_endpoint(client):
    resp = client.get("/api/games", params={"season": 2023})
    assert resp.status_code == 200
    games = resp.json()
    assert len(games) == 2
    assert games[0]["home_team"] == "Georgia"

    # Week filter narrows to one game.
    wk1 = client.get("/api/games", params={"season": 2023, "week": 1}).json()
    assert len(wk1) == 1 and wk1[0]["id"] == 401520281


def test_game_detail_endpoint(client):
    resp = client.get("/api/games/401520281")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["plays"]) == 5
    assert len(body["drives"]) == 2
    assert any(p["points_scored"] == 6 for p in body["plays"])
    assert body["team_stats"]  # box score present


def test_game_detail_404(client):
    assert client.get("/api/games/999").status_code == 404


def test_analytics_endpoint(client):
    resp = client.get("/api/analytics/scoring-by-field-position", params={"season": 2023})
    assert resp.status_code == 200
    buckets = {r["bucket"] for r in resp.json()}
    assert "Red zone (0-10)" in buckets


def test_team_endpoint(client):
    resp = client.get("/api/teams/Georgia", params={"season": 2023})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["record"] == "2-0"
    assert len(body["game_log"]) == 2


def test_team_endpoint_404(client):
    assert client.get("/api/teams/Nobody", params={"season": 2023}).status_code == 404


def test_players_search_and_detail(client):
    search = client.get("/api/players", params={"q": "beck", "season": 2023})
    assert search.status_code == 200
    assert search.json()[0]["player_id"] == 4429795

    detail = client.get("/api/players/4429795", params={"season": 2023})
    assert detail.status_code == 200
    body = detail.json()
    assert body["profile"]["position"] == "QB"
    assert any(s["stat_type"] == "YDS" and s["total"] == 518.0 for s in body["season_stats"])


def test_advanced_analytics_endpoints(client):
    assert client.get("/api/analytics/success-rate", params={"season": 2023}).status_code == 200
    ppa = client.get("/api/analytics/ppa-by-down", params={"season": 2023, "team": "Georgia"})
    assert ppa.status_code == 200
    assert all(r["down"] in (1, 2, 3, 4) for r in ppa.json())


def test_plotly_js_served_offline(client):
    resp = client.get("/vendor/plotly.min.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "Plotly" in resp.text


def test_html_pages(client):
    assert client.get("/").status_code == 200
    game_page = client.get("/games/401520281")
    assert game_page.status_code == 200
    assert "Georgia" in game_page.text

    assert client.get("/teams", params={"season": 2023}).status_code == 200
    team_page = client.get("/teams/Georgia", params={"season": 2023})
    assert team_page.status_code == 200
    assert "chart-trend" in team_page.text  # chart containers present

    assert client.get("/players", params={"q": "beck"}).status_code == 200
    player_page = client.get("/players/4429795", params={"season": 2023})
    assert player_page.status_code == 200
    assert "Carson Beck" in player_page.text
