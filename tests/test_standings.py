"""Tests for conference standings."""

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


def test_list_conferences(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        confs = q.list_conferences(s, 2023)
    assert "SEC" in confs and "Mid-American" in confs


def test_conference_standings_order(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        sec = q.conference_standings(s, 2023, "SEC")
    teams = [t["team"] for t in sec]
    assert teams == ["Georgia", "Alabama"]  # Georgia 1-0 conf ranks above Alabama 0-1
    ga = sec[0]
    assert ga["conf_record"] == "1-0" and ga["overall_record"] == "2-0"
    assert sec[1]["conf_record"] == "0-1"


def test_standings_endpoint_and_page(client):
    resp = client.get("/api/standings", params={"season": 2023, "conference": "SEC"})
    assert resp.status_code == 200
    assert resp.json()[0]["team"] == "Georgia"
    page = client.get("/standings", params={"season": 2023, "conference": "SEC"})
    assert page.status_code == 200 and "Georgia" in page.text
