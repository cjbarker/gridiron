"""Tests for drive-level analytics."""

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


def test_drive_outcomes(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        ga = {r["drive_result"]: r["drives"] for r in q.drive_outcomes(s, 2023, "Georgia")}
        al = {r["drive_result"]: r["drives"] for r in q.drive_outcomes(s, 2023, "Alabama")}
    assert ga.get("TD") == 2  # Georgia scored TDs on both its drives
    assert al.get("FG") == 1  # Alabama's lone drive ended in a field goal


def test_drive_efficiency(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        eff = q.drive_efficiency(s, "Georgia", 2023)
    assert eff["drives"] == 2
    assert eff["scoring_pct"] == 100.0
    assert eff["points_per_drive"] == 7.0  # 14 points / 2 drives
    assert eff["yards_per_drive"] == 77.5  # (75 + 80) / 2


def test_drive_endpoints(client):
    outcomes = client.get("/api/analytics/drive-outcomes", params={"season": 2023, "team": "Georgia"})
    assert outcomes.status_code == 200
    assert any(r["drive_result"] == "TD" for r in outcomes.json())
    eff = client.get("/api/analytics/drive-efficiency", params={"team": "Georgia", "season": 2023})
    assert eff.status_code == 200 and eff.json()["scoring_pct"] == 100.0


def test_team_page_has_drive_panel(client):
    page = client.get("/teams/Georgia", params={"season": 2023})
    assert page.status_code == 200
    assert "Pts / drive" in page.text and "chart-drive_outcomes" in page.text
