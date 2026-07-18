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
    assert len(games) == 1
    assert games[0]["home_team"] == "Georgia"


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


def test_html_pages(client):
    assert client.get("/").status_code == 200
    game_page = client.get("/games/401520281")
    assert game_page.status_code == 200
    assert "Georgia" in game_page.text
