"""Tests for coaching-record ingestion and views."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gridiron.analytics import queries as q
from gridiron.db.models import CoachSeason
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


@pytest.fixture()
def client(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    from gridiron.api.main import app

    return TestClient(app)


def test_coaches_ingested_for_season(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        # Only 2023 season entries land (Kirby 2023 + Saban 2023); Kirby 2022 not yet.
        assert s.scalar(select(func.count()).select_from(CoachSeason)) == 2
        ga = q.team_coaches(s, "Georgia", 2023)
    assert ga[0]["coach"] == "Kirby Smart" and ga[0]["record"] == "13-1"


def test_coach_career_across_two_seasons(db_env, fixture_source):
    # Ingest the fixture at both years so Kirby's 2022 + 2023 rows both land.
    ingest_season(fixture_source, 2022)
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        career = q.coach_career(s, "Kirby Smart")
    assert [x["season"] for x in career["seasons"]] == [2022, 2023]
    assert career["record"] == "28-1"  # 15-0 + 13-1
    assert career["teams"] == ["Georgia"]


def test_winningest_coaches_order(db_env, fixture_source):
    ingest_season(fixture_source, 2022)
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = q.winningest_coaches(s, min_games=1)
    assert rows[0]["coach"] == "Kirby Smart" and rows[0]["wins"] == 28


def test_coaches_idempotent(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    ingest_season(fixture_source, 2023)  # re-run
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(CoachSeason)) == 2


def test_coaching_endpoints_and_pages(client):
    career = client.get("/api/coaches/Kirby Smart")
    assert career.status_code == 200 and career.json()["record"] == "13-1"
    assert client.get("/api/coaches/Nobody").status_code == 404
    winners = client.get("/api/analytics/winningest-coaches")
    assert winners.status_code == 200 and any(c["coach"] == "Kirby Smart" for c in winners.json())
    # Pages render.
    assert "Winningest coaches" in client.get("/coaches").text
    assert "Season by season" in client.get("/coaches/Kirby Smart").text
    # Team page shows the head-coach panel.
    assert "Head coach" in client.get("/teams/Georgia", params={"season": 2023}).text
