"""Tests for player win-probability-added (WPA)."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from gridiron.analytics import queries as q
from gridiron.db.session import session_scope
from gridiron.ingest import transforms as tf
from gridiron.ingest.pipeline import ingest_season

PBP_PARQUET = Path(__file__).parent / "fixtures" / "pbp" / "play_by_play_2022.parquet"


@pytest.fixture()
def client(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    from gridiron.api.main import app

    return TestClient(app)


def test_primary_player_attribution():
    # A rush credits the rusher; a completed pass credits the passer.
    rush = tf.to_play({"playType": "Rush", "rusher_player_name": "RB", "wpa": 0.1})
    assert rush.wpa_player == "RB" and rush.wpa == 0.1
    pass_td = tf.to_play(
        {"playType": "Passing Touchdown", "passer_player_name": "QB",
         "receiver_player_name": "WR", "wpa": 0.2}
    )
    assert pass_td.wpa_player == "QB"  # passer beats receiver


def test_wpa_from_before_after_delta():
    p = tf.to_play({"playType": "Rush", "rusher_player_name": "RB",
                    "wp_before": 0.40, "wp_after": 0.55})
    assert p.wpa == pytest.approx(0.15)


def test_player_wpa_leaders_order(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = q.player_wpa_leaders(s, 2023, min_plays=1)
    leaders = [r["player"] for r in rows]
    # Carson Beck (0.09) tops; Kendall Milton (0.03+0.04=0.07) next.
    assert leaders[0] == "Carson Beck"
    assert leaders[1] == "Kendall Milton"
    beck = next(r for r in rows if r["player"] == "Carson Beck")
    assert beck["total_wpa"] == 0.09


def test_player_wpa_by_name(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        milton = q.player_wpa(s, "Kendall Milton", 2023)
    assert milton["plays"] == 2 and milton["total_wpa"] == 0.07


def test_parquet_wpa_maps():
    rows = pq.read_table(PBP_PARQUET).to_pylist()
    plays = [tf.to_play(r, season=2022) for r in rows]
    assert any(p.wpa is not None for p in plays)
    assert any(p.wpa_player for p in plays)


def test_wpa_endpoints_and_pages(client):
    leaders = client.get("/api/analytics/player-wpa-leaders", params={"season": 2023})
    assert leaders.status_code == 200 and leaders.json()[0]["player"] == "Carson Beck"
    # Beck's player page shows a WPA pill.
    page = client.get("/players/4429795", params={"season": 2023})
    assert page.status_code == 200 and "WPA 0.09" in page.text
    assert "chart-player_wpa" in client.get("/leaders", params={"season": 2023}).text
