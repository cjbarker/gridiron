"""Tests for player-vs-player comparison."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gridiron.analytics import queries as q
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season

BECK = 4429795  # Carson Beck (Georgia QB)
BOWERS = 4432648  # Brock Bowers (Georgia TE)


@pytest.fixture()
def client(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    from gridiron.api.main import app

    return TestClient(app)


def test_player_compare_query(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        cmp = q.player_compare(s, BECK, BOWERS, 2023)
    assert cmp["a"]["profile"]["name"] == "Carson Beck"
    assert cmp["b"]["profile"]["name"] == "Brock Bowers"
    # Beck has passing YDS (518); Bowers has receiving YDS. Both appear as rows.
    by_key = {(r["category"], r["stat_type"]): r for r in cmp["stats"]}
    assert by_key[("passing", "YDS")]["a"] == 518.0
    assert by_key[("passing", "YDS")]["b"] is None
    assert ("receiving", "YDS") in by_key


def test_player_compare_api(client):
    resp = client.get("/api/players/compare", params={"a": BECK, "b": BOWERS, "season": 2023})
    assert resp.status_code == 200
    assert resp.json()["a"]["profile"]["name"] == "Carson Beck"


def test_player_compare_page_by_name(client):
    # Names resolve via player search.
    resp = client.get("/players/compare", params={"a": "beck", "b": "bowers", "season": 2023})
    assert resp.status_code == 200
    assert "Carson Beck" in resp.text and "chart-compare" in resp.text


def test_player_compare_page_empty(client):
    assert client.get("/players/compare", params={"season": 2023}).status_code == 200
