"""Tests for matchup prediction + Monte-Carlo simulation (`analytics/predict.py`)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gridiron.analytics import predict as pr
from gridiron.analytics import ratings as rt
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


@pytest.fixture()
def client(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    from gridiron.api.main import app

    return TestClient(app)


def _rating(team: str, srs: float, off: float = 0.0, dfn: float = 0.0) -> rt.TeamRating:
    return rt.TeamRating(team, 10, srs, off, dfn, 0.0, 0.0, 28.0, 28.0)


def _season(ratings: list[rt.TeamRating], *, hfa: float = 3.0, std: float = 10.0) -> rt.SeasonRatings:
    return rt.SeasonRatings(2023, {r.team: r for r in ratings}, 28.0, hfa, std)


def test_win_prob_is_monotonic_and_invertible():
    assert pr.win_prob_from_margin(10) > pr.win_prob_from_margin(0) == 0.5
    assert abs(pr.margin_from_win_prob(pr.win_prob_from_margin(7)) - 7) < 1e-6


def test_neutral_site_drops_home_field():
    ra, rb = _rating("A", 5), _rating("B", 0)
    sra, srb = _season([ra], hfa=3.0), _season([rb], hfa=3.0)
    home = pr.ratings_prediction(ra, sra, rb, srb, "A", "B", neutral=False, home="a", era_adjusted=False)
    neutral = pr.ratings_prediction(ra, sra, rb, srb, "A", "B", neutral=True, home=None, era_adjusted=False)
    assert round(home.proj_margin - neutral.proj_margin, 1) == 3.0


def test_home_choice_flips_margin_sign_for_equal_teams():
    ra, rb = _rating("A", 0), _rating("B", 0)
    sra, srb = _season([ra], hfa=4.0), _season([rb], hfa=4.0)
    a_home = pr.ratings_prediction(ra, sra, rb, srb, "A", "B", neutral=False, home="a", era_adjusted=False)
    b_home = pr.ratings_prediction(ra, sra, rb, srb, "A", "B", neutral=False, home="b", era_adjusted=False)
    assert a_home.proj_margin > 0 > b_home.proj_margin


def test_era_adjust_is_noop_within_a_season():
    ra = _rating("A", 8)
    sr = _season([ra], std=8.0)
    assert pr._era_scaled(ra, sr, 8.0, False) == pr._era_scaled(ra, sr, 8.0, True)


def test_simulation_is_seed_reproducible():
    s1 = pr.simulate(7.0, 55.0, sims=1000, seed=5)
    s2 = pr.simulate(7.0, 55.0, sims=1000, seed=5)
    assert s1.margins == s2.margins
    assert 0.0 <= s1.win_prob_a <= 1.0
    assert s1.margin_p5 <= s1.margin_p95


def test_predict_matchup_end_to_end(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        res = pr.predict_matchup(s, "Georgia", 2023, "Alabama", 2023, sims=500, seed=1)
    assert res is not None
    pred = res["prediction"]
    assert 0.0 <= pred["win_prob_a"] <= 1.0
    assert set(pred["components"]["a"]) >= {"srs", "adj_off", "adj_def", "sos", "form"}
    assert len(res["margins"]) == 500


def test_predict_matchup_unknown_team_returns_none(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        assert pr.predict_matchup(s, "Nobody", 2023, "Alabama", 2023) is None


def test_logistic_falls_back_when_untrainable(db_env, fixture_source):
    # The tiny fixture has only home wins → the LR can't learn two classes, so the
    # engine must fall back to ratings rather than error.
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        res = pr.predict_matchup(s, "Georgia", 2023, "Alabama", 2023, model="logistic", sims=200)
    assert res["prediction"]["model"] == "ratings"
    assert res["ml_fell_back"] is True


def test_predict_api(client):
    resp = client.get(
        "/api/predict",
        params={"a": "Georgia", "season_a": 2023, "b": "Alabama", "season_b": 2023, "sims": 300},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert 0.0 <= body["prediction"]["win_prob_a"] <= 1.0
    assert "margins" not in body  # trimmed from the JSON payload
    assert client.get(
        "/api/predict",
        params={"a": "Nobody", "season_a": 2023, "b": "Alabama", "season_b": 2023},
    ).status_code == 404


def test_predict_backtest_api(client):
    resp = client.get("/api/predict/backtest", params={"season": 2023})
    assert resp.status_code == 200
    assert resp.json()["season"] == 2023


def test_predict_page_renders(client):
    resp = client.get(
        "/predict",
        params={"a": "Georgia", "season_a": 2023, "b": "Alabama", "season_b": 2023},
    )
    assert resp.status_code == 200
    assert "chart-dist" in resp.text and "Projected score" in resp.text
    assert client.get("/predict").status_code == 200  # no selection


def test_backtest_page_renders(client):
    resp = client.get("/predict/backtest", params={"season": 2023})
    assert resp.status_code == 200
    assert "chart-cal" in resp.text
