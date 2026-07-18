"""Bulk parquet backfill: cfbfastR column mapping, EPA/WP, stubs, idempotency."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
from sqlalchemy import func, select

from gridiron.db.models import Game, Play
from gridiron.db.session import session_scope
from gridiron.ingest import transforms as tf
from gridiron.ingest.pipeline import ingest_season
from gridiron.ingest.sources import EmptySource, FixtureSource

PBP_PARQUET = Path(__file__).parent / "fixtures" / "pbp" / "play_by_play_2022.parquet"
GAMES_2022 = Path(__file__).parent / "fixtures" / "season2022_games"
GAME_ID = 401403910


def _loader(year: int) -> list[dict]:
    """Read the committed fixture parquet instead of downloading (offline)."""
    return pq.read_table(PBP_PARQUET).to_pylist()


def _play_counts():
    with session_scope() as s:
        return {
            "games": s.scalar(select(func.count()).select_from(Game)),
            "plays": s.scalar(select(func.count()).select_from(Play)),
        }


def test_to_game_stub_final_score():
    rows = pq.read_table(PBP_PARQUET).to_pylist()
    stub = tf.to_game_stub(2022, GAME_ID, rows)
    assert stub.home_team == "Ohio State" and stub.away_team == "Notre Dame"
    assert stub.home_points == 21 and stub.away_points == 10  # max score per side
    assert stub.season == 2022 and stub.week == 1


def test_parquet_columns_map_to_play():
    rows = pq.read_table(PBP_PARQUET).to_pylist()
    plays = {p.id: p for p in (tf.to_play(r, season=2022) for r in rows)}
    td = plays[4]  # Ohio State passing TD
    assert td.offense == "Ohio State"  # from pos_team
    assert td.defense == "Notre Dame"  # from def_pos_team
    assert td.epa == 3.9  # from EPA
    assert td.wp == 0.66  # from wp_before
    assert td.clock_minutes == 1 and td.clock_seconds == 5  # dotted clock cols
    assert td.points_scored == 6 and td.season == 2022
    assert td.offense_score == 14  # from pos_team_score
    fg = plays[2]  # Notre Dame field goal
    assert fg.play_type == "Field Goal Good" and fg.points_scored == 3


def test_offline_backfill_with_stub_games(db_env):
    ingest_season(
        EmptySource(),
        2022,
        plays_source="parquet",
        parquet_loader=_loader,
        stub_games=True,
        with_stats=False,
        with_rosters=False,
    )
    with session_scope() as s:
        game = s.get(Game, GAME_ID)
        assert game is not None and game.home_points == 21 and game.away_points == 10
        plays = s.execute(select(Play).where(Play.game_id == GAME_ID)).scalars().all()
        assert len(plays) == 6
        # EPA/WP/season populated from the parquet.
        assert all(p.season == 2022 for p in plays)
        assert all(p.epa is not None and p.wp is not None for p in plays)
        assert s.scalar(select(func.sum(Play.points_scored))) == 6 + 3 + 6 + 6 + 6  # 4 TD + 1 FG


def test_backfill_is_idempotent(db_env):
    kwargs = dict(
        plays_source="parquet", parquet_loader=_loader, stub_games=True,
        with_stats=False, with_rosters=False,
    )
    ingest_season(EmptySource(), 2022, **kwargs)
    first = _play_counts()
    ingest_season(EmptySource(), 2022, **kwargs)  # re-run
    second = _play_counts()
    assert first == second == {"games": 1, "plays": 6}


def test_hybrid_games_from_source_plays_from_parquet(db_env):
    # Games come from a (fixture) source; plays from the parquet attach to them.
    ingest_season(
        FixtureSource(GAMES_2022),
        2022,
        plays_source="parquet",
        parquet_loader=_loader,
        stub_games=False,  # no synthesis — rely on the ingested game
        with_stats=False,
        with_rosters=False,
    )
    with session_scope() as s:
        assert s.get(Game, GAME_ID).home_team == "Ohio State"
        assert s.scalar(select(func.count()).select_from(Play)) == 6
        assert s.scalar(select(func.count()).select_from(Game)) == 1
