"""Composable play filters.

A :class:`PlayFilter` bundles the dimensions a user can slice by, and
:func:`apply_play_filter` turns it into ``WHERE`` clauses on any ``Play``-based
``select``. Team/game-relational dimensions (week, home/away, conference,
vs-ranked) use correlated ``EXISTS`` subqueries keyed on ``Play.game_id`` /
``Play.offense`` / ``Play.defense`` so the filter composes with existing
``group_by`` aggregates without adding joins.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from sqlalchemy import and_, exists, select

from gridiron.db.models import Game, Play, Ranking, Team


@dataclass
class PlayFilter:
    season: int | None = None
    team: str | None = None  # offense team
    week_min: int | None = None
    week_max: int | None = None
    home_away: str | None = None  # "home" | "away"
    conference: str | None = None  # offense team's conference
    vs_ranked: bool = False  # opponent (defense) was AP-ranked that season
    down: int | None = None
    distance_min: int | None = None
    distance_max: int | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) in (None, False) for f in fields(self))


def apply_play_filter(stmt, f: PlayFilter):
    """Add WHERE clauses for a :class:`PlayFilter` to a Play-based select."""
    if f.season is not None:
        stmt = stmt.where(Play.season == f.season)
    if f.team is not None:
        stmt = stmt.where(Play.offense == f.team)
    if f.down is not None:
        stmt = stmt.where(Play.down == f.down)
    if f.distance_min is not None:
        stmt = stmt.where(Play.distance >= f.distance_min)
    if f.distance_max is not None:
        stmt = stmt.where(Play.distance <= f.distance_max)

    if f.week_min is not None or f.week_max is not None:
        conds = [Game.id == Play.game_id]
        if f.week_min is not None:
            conds.append(Game.week >= f.week_min)
        if f.week_max is not None:
            conds.append(Game.week <= f.week_max)
        stmt = stmt.where(exists(select(Game.id).where(and_(*conds))))

    if f.home_away == "home":
        stmt = stmt.where(
            exists(select(Game.id).where(and_(Game.id == Play.game_id, Game.home_team == Play.offense)))
        )
    elif f.home_away == "away":
        stmt = stmt.where(
            exists(select(Game.id).where(and_(Game.id == Play.game_id, Game.away_team == Play.offense)))
        )

    if f.conference is not None:
        stmt = stmt.where(
            exists(
                select(Team.id).where(
                    and_(Team.school == Play.offense, Team.conference == f.conference)
                )
            )
        )

    if f.vs_ranked:
        ranked = select(Ranking.id).where(Ranking.team == Play.defense)
        if f.season is not None:
            ranked = ranked.where(Ranking.season == f.season)
        stmt = stmt.where(exists(ranked))

    return stmt
