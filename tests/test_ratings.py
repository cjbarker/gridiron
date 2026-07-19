"""Tests for the SRS power-rating calculator (`analytics/ratings.py`)."""

from __future__ import annotations

from gridiron.analytics import ratings as rt
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


def _round_robin() -> list[rt.GameRow]:
    # A clearly beats B clearly beats C, home/away balanced across two cycles.
    return [
        rt.GameRow(1, "A", "B", 31, 17, False, 1),
        rt.GameRow(2, "B", "C", 24, 20, False, 2),
        rt.GameRow(3, "A", "C", 35, 10, False, 3),
        rt.GameRow(4, "B", "A", 20, 27, False, 4),
        rt.GameRow(5, "C", "B", 13, 26, False, 5),
        rt.GameRow(6, "C", "A", 7, 45, False, 6),
    ]


def test_srs_orders_and_centers():
    sr = rt.compute_ratings(2023, _round_robin())
    assert sr.get("A").srs > sr.get("B").srs > sr.get("C").srs
    assert abs(sum(t.srs for t in sr.teams.values())) < 0.5  # centered near zero


def test_adjusted_offense_and_defense_direction():
    sr = rt.compute_ratings(2023, _round_robin())
    a, c = sr.get("A"), sr.get("C")
    assert a.adj_off > c.adj_off
    assert a.adj_def > c.adj_def  # A allows fewer → higher (better) adj_def
    assert sr.get("A").form > 0  # A's recent games were wins


def test_home_field_fit_from_data():
    games = [rt.GameRow(1, "A", "B", 30, 20, False, 1), rt.GameRow(2, "A", "B", 24, 21, False, 2)]
    assert rt.home_field_advantage(games) == 6.5
    # Neutral games are excluded; with none left we fall back to the default.
    assert rt.home_field_advantage([rt.GameRow(1, "A", "B", 30, 20, True, 1)]) == rt.DEFAULT_HFA


def test_margin_cap_damps_blowouts():
    games = [rt.GameRow(1, "A", "B", 100, 0, False, 1), rt.GameRow(2, "B", "A", 10, 7, False, 2)]
    sr = rt.compute_ratings(2023, games)
    assert sr.get("A").srs <= rt.MARGIN_CAP + 5  # 100-0 got clamped, not taken literally


def test_empty_season_is_safe():
    sr = rt.compute_ratings(2023, [])
    assert sr.teams == {}
    assert sr.hfa == rt.DEFAULT_HFA


def test_team_ratings_and_through_week(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        sr = rt.team_ratings(s, 2023)
        assert sr.get("Georgia") is not None
        # through_week=1 keeps only weeks < 1 → no games → empty ratings.
        assert rt.team_ratings(s, 2023, through_week=1).teams == {}
