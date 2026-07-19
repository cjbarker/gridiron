"""Tests for team comparison (queries + API + page)."""

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


def test_team_compare_query(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        cmp = q.team_compare(s, "Georgia", "Alabama", 2023)
    assert cmp["a"]["summary"]["record"] == "2-0"
    assert cmp["b"]["summary"]["record"] == "0-1"
    assert [g["game_id"] for g in cmp["head_to_head"]] == [401520281]
    assert cmp["a"]["success_rate"] == 0.75


def test_compare_api(client):
    resp = client.get("/api/compare", params={"a": "Georgia", "b": "Alabama", "season": 2023})
    assert resp.status_code == 200
    body = resp.json()
    assert body["a"]["summary"]["record"] == "2-0"
    assert len(body["head_to_head"]) == 1


def test_compare_page(client):
    resp = client.get("/compare", params={"a": "Georgia", "b": "Alabama", "season": 2023})
    assert resp.status_code == 200
    assert "chart-compare" in resp.text
    assert "Head-to-head" in resp.text


def test_compare_page_no_selection(client):
    # Landing on /compare with no teams chosen still renders (form only).
    assert client.get("/compare", params={"season": 2023}).status_code == 200
