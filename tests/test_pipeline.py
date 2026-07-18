"""End-to-end ingest tests: idempotency and correctness against the fixtures."""

from __future__ import annotations

from sqlalchemy import func, select

from gridiron.db.models import (
    Drive,
    Game,
    Play,
    PlayerGameStat,
    Ranking,
    Team,
    TeamGameStat,
)
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


def _counts():
    with session_scope() as s:
        return {
            "teams": s.scalar(select(func.count()).select_from(Team)),
            "games": s.scalar(select(func.count()).select_from(Game)),
            "drives": s.scalar(select(func.count()).select_from(Drive)),
            "plays": s.scalar(select(func.count()).select_from(Play)),
            "player_game_stats": s.scalar(select(func.count()).select_from(PlayerGameStat)),
            "team_game_stats": s.scalar(select(func.count()).select_from(TeamGameStat)),
            "rankings": s.scalar(select(func.count()).select_from(Ranking)),
        }


def test_ingest_populates_all_tables(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    c = _counts()
    assert c["teams"] == 2
    assert c["games"] == 1
    assert c["drives"] == 2
    assert c["plays"] == 5
    assert c["player_game_stats"] == 4  # 2 UGA passing + 1 UGA recv + 1 ALA rush
    assert c["team_game_stats"] == 6  # 3 stats x 2 teams
    assert c["rankings"] == 2


def test_ingest_is_idempotent(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    first = _counts()
    ingest_season(fixture_source, 2023)  # run again
    second = _counts()
    assert first == second, "re-ingesting a season must not duplicate rows"


def test_scoring_play_has_points_and_yardline(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        td = s.execute(
            select(Play).where(Play.play_type == "Passing Touchdown")
        ).scalar_one()
        assert td.points_scored == 6
        assert td.yards_to_goal == 15
        assert td.scoring is True
        total_points = s.scalar(select(func.sum(Play.points_scored)))
        assert total_points == 10  # 6 + 1 + 3
