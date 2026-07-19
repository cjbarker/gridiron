"""Tests for the composable PlayFilter and its query integration."""

from __future__ import annotations

from gridiron.analytics import queries as q
from gridiron.analytics.filters import PlayFilter
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


def _rate(rows, team):
    return next((r["success_rate"] for r in rows if r["team"] == team), None)


def test_home_away_filter(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        home = q.success_rate(s, flt=PlayFilter(season=2023, team="Georgia", home_away="home"))
        away = q.success_rate(s, flt=PlayFilter(season=2023, team="Georgia", home_away="away"))
    assert _rate(home, "Georgia") == 0.75  # Georgia played both games at home
    assert away == []  # ...and none away


def test_vs_ranked_filter(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        # Alabama is AP-ranked; Ball State is not. Georgia's plays vs a ranked
        # opponent are only those from the Alabama game.
        ranked = q.play_type_mix(s, flt=PlayFilter(season=2023, team="Georgia", vs_ranked=True))
        all_plays = q.play_type_mix(s, flt=PlayFilter(season=2023, team="Georgia"))
    assert sum(r["plays"] for r in ranked) == 3  # pass TD, extra point, FG missed
    assert sum(r["plays"] for r in all_plays) > 3


def test_week_range_filter(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        wk1 = q.play_type_mix(s, flt=PlayFilter(season=2023, team="Georgia", week_max=1))
        wk2 = q.play_type_mix(s, flt=PlayFilter(season=2023, team="Georgia", week_min=2))
    assert sum(r["plays"] for r in wk1) == 3  # game1 Georgia offensive plays
    assert sum(r["plays"] for r in wk2) == 3  # game2 Georgia offensive plays


def test_conference_filter(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        sec = q.play_type_mix(s, flt=PlayFilter(season=2023, team="Georgia", conference="SEC"))
        big_ten = q.play_type_mix(s, flt=PlayFilter(season=2023, team="Georgia", conference="Big Ten"))
    assert sec  # Georgia is in the SEC
    assert big_ten == []


def test_down_and_distance_filter(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        first = {r["down"] for r in q.ppa_by_down(s, flt=PlayFilter(season=2023, down=1))}
        long = q.play_type_mix(s, flt=PlayFilter(season=2023, distance_min=8))
    assert first <= {1}
    assert isinstance(long, list)


def test_empty_filter_flag():
    assert PlayFilter().is_empty()
    assert not PlayFilter(down=3).is_empty()
