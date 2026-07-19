"""Tests for recruiting rankings + transfer-portal ingestion and views."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gridiron.analytics import queries as q
from gridiron.db.models import TeamRecruitingRank, Transfer
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


@pytest.fixture()
def client(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    from gridiron.api.main import app

    return TestClient(app)


def test_recruiting_ingested(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(TeamRecruitingRank)) == 2
        assert s.scalar(select(func.count()).select_from(Transfer)) == 3
        rec = q.team_recruiting(s, "Georgia", 2023)
    assert rec["rank"] == 1 and rec["points"] == 313.02


def test_team_transfers_in_and_out(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        ga = q.team_transfers(s, "Georgia", 2023)
        al = q.team_transfers(s, "Alabama", 2023)
    assert [t["player"] for t in ga["incoming"]] == ["Rara Thomas"]
    assert ga["outgoing"] == []
    # Alabama has one in (Domani Jackson) and one out (Justice Haynes).
    assert [t["player"] for t in al["incoming"]] == ["Domani Jackson"]
    assert [t["player"] for t in al["outgoing"]] == ["Justice Haynes"]


def test_recruiting_is_idempotent(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    ingest_season(fixture_source, 2023)  # re-run
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Transfer)) == 3


def test_recruiting_endpoint_and_panel(client):
    resp = client.get("/api/teams/Georgia/recruiting", params={"season": 2023})
    assert resp.status_code == 200
    assert resp.json()["recruiting"]["rank"] == 1
    page = client.get("/teams/Georgia", params={"season": 2023})
    assert page.status_code == 200
    assert "Recruiting &amp; transfers" in page.text and "Rara Thomas" in page.text
