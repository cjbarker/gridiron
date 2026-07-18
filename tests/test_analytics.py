"""Tests for the derived analytics queries."""

from __future__ import annotations

from gridiron.analytics import queries as q
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


def test_scoring_by_field_position(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["bucket"]: r for r in q.scoring_by_field_position(s, 2023)}
    assert rows["Red zone (0-10)"]["points"] == 1  # extra point at ytg 3
    assert rows["11-20"]["points"] == 6  # passing TD at ytg 15
    assert rows["21-40"]["points"] == 3  # field goal at ytg 22


def test_scoring_type_breakdown(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["score_type"]: r for r in q.scoring_type_breakdown(s, 2023)}
    assert rows["Touchdown (6)"]["plays"] == 1
    assert rows["Field goal (3)"]["points"] == 3
    assert rows["Extra point (1)"]["plays"] == 1


def test_field_goal_success_by_distance(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["bucket"]: r for r in q.field_goal_success_by_distance(s, 2023)}
    # One made (ytg 22) and one missed (ytg 35), both in the 21-40 bucket.
    assert rows["21-40"]["attempts"] == 2
    assert rows["21-40"]["made"] == 1
    assert rows["21-40"]["pct"] == 50.0


def test_ppa_leaders_low_threshold(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = q.ppa_leaders(s, 2023, min_plays=1)
    teams = {r["team"] for r in rows}
    assert "Georgia" in teams and "Alabama" in teams


def test_team_scoring_summary(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["team"]: r for r in q.team_scoring_summary(s, 2023)}
    assert rows["Georgia"]["points_for"] == 27
    assert rows["Alabama"]["points_for"] == 24
