"""Tests for the derived analytics queries."""

from __future__ import annotations

from gridiron.analytics import queries as q
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


def test_scoring_by_field_position(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["bucket"]: r for r in q.scoring_by_field_position(s, 2023)}
    # Red zone: game1 PAT (1) + game2 rushing TD (6) + game2 PAT (1) = 8
    assert rows["Red zone (0-10)"]["points"] == 8
    assert rows["11-20"]["points"] == 6  # passing TD at ytg 15
    assert rows["21-40"]["points"] == 3  # field goal at ytg 22


def test_scoring_by_field_position_team_scoped(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["bucket"]: r for r in q.scoring_by_field_position(s, 2023, team="Alabama")}
    # Alabama's only points are the game1 field goal at ytg 22.
    assert rows["21-40"]["points"] == 3
    assert "Red zone (0-10)" not in rows


def test_scoring_type_breakdown(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["score_type"]: r for r in q.scoring_type_breakdown(s, 2023)}
    assert rows["Touchdown (6)"]["plays"] == 2  # game1 pass TD + game2 rush TD
    assert rows["Field goal (3)"]["points"] == 3
    assert rows["Extra point (1)"]["plays"] == 2


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
    assert rows["Georgia"]["points_for"] == 72  # 27 + 45 across two games
    assert rows["Alabama"]["points_for"] == 24


def test_team_season_summary_and_game_log(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        summary = q.team_season_summary(s, "Georgia", 2023)
        log = q.team_game_log(s, "Georgia", 2023)
    assert summary["record"] == "2-0"
    assert summary["points_for"] == 72 and summary["points_against"] == 27
    assert summary["ppg"] == 36.0
    assert [g["opponent"] for g in log] == ["Alabama", "Ball State"]
    assert all(g["result"] == "W" for g in log)


def test_team_rankings_history(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        hist = q.team_rankings_history(s, "Georgia", 2023)
    assert hist and hist[0]["rank"] == 1 and hist[0]["poll"] == "AP Top 25"


def test_player_views(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        profile = q.player_profile(s, 4429795)
        stats = {(r["category"], r["stat_type"]): r for r in q.player_season_stats(s, 4429795, 2023)}
        log = q.player_game_log(s, 4429795, 2023)
        found = q.player_search(s, "beck", 2023)
    assert profile["name"] == "Carson Beck" and profile["position"] == "QB"
    assert stats[("passing", "YDS")]["total"] == 518.0  # 306 + 212
    assert stats[("passing", "TD")]["total"] == 3.0
    assert len(log) == 2
    assert found and found[0]["player_id"] == 4429795


def test_success_rate(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        rows = {r["team"]: r for r in q.success_rate(s, 2023)}
    assert rows["Georgia"]["success_rate"] == 0.75
    assert rows["Alabama"]["success_rate"] == 0.0


def test_explosiveness_and_ppa_by_down(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        expl = {r["team"]: r for r in q.explosiveness(s, 2023)}
        by_down = {r["down"]: r for r in q.ppa_by_down(s, 2023)}
    assert 0.0 <= expl["Georgia"]["explosive_rate"] <= 1.0
    assert set(by_down.keys()) <= {1, 2, 3, 4}
    assert by_down[1]["plays"] >= 1
