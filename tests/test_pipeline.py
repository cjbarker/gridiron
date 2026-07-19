"""End-to-end ingest tests: idempotency and correctness against the fixtures."""

from __future__ import annotations

from sqlalchemy import func, select

from gridiron.db.models import (
    BettingLine,
    Drive,
    Game,
    Play,
    Player,
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
            "players": s.scalar(select(func.count()).select_from(Player)),
            "player_game_stats": s.scalar(select(func.count()).select_from(PlayerGameStat)),
            "team_game_stats": s.scalar(select(func.count()).select_from(TeamGameStat)),
            "rankings": s.scalar(select(func.count()).select_from(Ranking)),
            "betting_lines": s.scalar(select(func.count()).select_from(BettingLine)),
        }


def test_ingest_populates_all_tables(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    c = _counts()
    assert c["teams"] == 3
    assert c["games"] == 2
    assert c["drives"] == 3
    assert c["plays"] == 8
    assert c["players"] == 3  # roster ingestion fills the players table
    assert c["player_game_stats"] == 6  # game1: 4, game2: Beck YDS + TD
    assert c["team_game_stats"] == 6  # 3 stats x 2 teams (game1 only)
    assert c["rankings"] == 2
    assert c["betting_lines"] == 2  # one line per game


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
        # game1: TD 6 + PAT 1 + FG 3 = 10; game2: TD 6 + PAT 1 = 7
        assert total_points == 17
