"""Tests for betting-line ingestion, display, and ATS records."""

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


def test_game_betting_lines(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        lines = q.game_betting_lines(s, 401520281)
    assert len(lines) == 1
    assert lines[0]["provider"] == "consensus"
    assert lines[0]["spread"] == -2.5
    assert lines[0]["over_under"] == 52.5


def test_team_ats_record(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        ga = q.team_ats_record(s, "Georgia", 2023)
        al = q.team_ats_record(s, "Alabama", 2023)
    # Georgia covered both (won by 3 as -2.5; won by 42 as -38.5); both games under.
    assert ga["ats"] == "2-0"
    assert ga["over_under"] == "0-2"
    assert ga["games_with_lines"] == 2
    # Alabama lost 24-27 as +2.5 underdog -> did not cover.
    assert al["ats"] == "0-1"


def test_lines_are_idempotent(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    ingest_season(fixture_source, 2023)  # re-run
    with session_scope() as s:
        assert len(q.game_betting_lines(s, 401520281)) == 1


def test_betting_endpoints_and_pages(client):
    lines = client.get("/api/games/401520281/lines")
    assert lines.status_code == 200 and lines.json()[0]["provider"] == "consensus"
    ats = client.get("/api/analytics/ats-record", params={"team": "Georgia", "season": 2023})
    assert ats.status_code == 200 and ats.json()["ats"] == "2-0"
    # Game page shows the line; team page shows the ATS record.
    assert "Betting lines" in client.get("/games/401520281").text
    assert "ATS 2-0" in client.get("/teams/Georgia", params={"season": 2023}).text
