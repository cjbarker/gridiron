"""Derived statistical queries.

These functions turn the raw ``plays``/``games`` tables into the kinds of
analysis the site is about — where on the field points come from, how scoring
breaks down by type, field-goal success by distance, play-type mix, and
efficiency (PPA/EPA) leaders. Each returns plain dicts, ready for JSON or a
template. All are read-only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from gridiron.db.models import Game, Play

# Field-position buckets by yards-to-goal (distance to opponent end zone).
_FP_BUCKETS = [
    (0, 10, "Red zone (0-10)"),
    (11, 20, "11-20"),
    (21, 40, "21-40"),
    (41, 60, "41-60"),
    (61, 80, "61-80"),
    (81, 100, "Backed up (81-100)"),
]


def _season_filter(stmt, season: int | None):
    if season is not None:
        stmt = stmt.where(Play.game_id.in_(select(Game.id).where(Game.season == season)))
    return stmt


def scoring_by_field_position(session: Session, season: int | None = None) -> list[dict[str, Any]]:
    """Count and total points of scoring plays, bucketed by field position.

    Answers the core question: *from what yardage on the field are points scored?*
    """
    bucket = case(
        *[
            (Play.yards_to_goal.between(lo, hi), label)
            for (lo, hi, label) in _FP_BUCKETS
        ],
        else_="Unknown",
    ).label("bucket")

    stmt = (
        select(
            bucket,
            func.count().label("scoring_plays"),
            func.coalesce(func.sum(Play.points_scored), 0).label("points"),
        )
        .where(Play.scoring.is_(True))
        .group_by(bucket)
    )
    stmt = _season_filter(stmt, season)
    order = {label: i for i, (_, _, label) in enumerate(_FP_BUCKETS)}
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    rows.sort(key=lambda r: order.get(r["bucket"], 99))
    return rows


def scoring_type_breakdown(session: Session, season: int | None = None) -> list[dict[str, Any]]:
    """Breakdown of scoring plays by point value (6=TD, 3=FG, 2=safety/2pt, 1=PAT)."""
    label = case(
        (Play.points_scored == 6, "Touchdown (6)"),
        (Play.points_scored == 3, "Field goal (3)"),
        (Play.points_scored == 2, "Safety / 2-pt (2)"),
        (Play.points_scored == 1, "Extra point (1)"),
        else_="Other",
    ).label("score_type")

    stmt = (
        select(
            label,
            func.count().label("plays"),
            func.coalesce(func.sum(Play.points_scored), 0).label("points"),
        )
        .where(Play.scoring.is_(True), Play.points_scored > 0)
        .group_by(label)
        .order_by(func.sum(Play.points_scored).desc())
    )
    stmt = _season_filter(stmt, season)
    return [dict(r._mapping) for r in session.execute(stmt)]


def field_goal_success_by_distance(
    session: Session, season: int | None = None
) -> list[dict[str, Any]]:
    """Field-goal make rate bucketed by distance (yards-to-goal)."""
    made = func.sum(case((Play.play_type == "Field Goal Good", 1), else_=0)).label("made")
    attempts = func.count().label("attempts")
    bucket = case(
        *[
            (Play.yards_to_goal.between(lo, hi), label)
            for (lo, hi, label) in _FP_BUCKETS
        ],
        else_="Unknown",
    ).label("bucket")

    stmt = (
        select(bucket, attempts, made)
        .where(Play.play_type.in_(["Field Goal Good", "Field Goal Missed"]))
        .group_by(bucket)
    )
    stmt = _season_filter(stmt, season)
    order = {label: i for i, (_, _, label) in enumerate(_FP_BUCKETS)}
    out = []
    for r in session.execute(stmt):
        m = r._mapping
        att = m["attempts"] or 0
        made_n = m["made"] or 0
        out.append(
            {
                "bucket": m["bucket"],
                "attempts": att,
                "made": made_n,
                "pct": round(100.0 * made_n / att, 1) if att else None,
            }
        )
    out.sort(key=lambda r: order.get(r["bucket"], 99))
    return out


def play_type_mix(
    session: Session, season: int | None = None, team: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Most common play types (optionally for a single offense)."""
    stmt = (
        select(Play.play_type, func.count().label("plays"))
        .group_by(Play.play_type)
        .order_by(func.count().desc())
        .limit(limit)
    )
    if team is not None:
        stmt = stmt.where(Play.offense == team)
    stmt = _season_filter(stmt, season)
    return [dict(r._mapping) for r in session.execute(stmt)]


def ppa_leaders(
    session: Session, season: int | None = None, min_plays: int = 200, limit: int = 25
) -> list[dict[str, Any]]:
    """Offensive efficiency leaders by average predicted points added (PPA/EPA).

    Uses ``epa`` when present (bulk parquet), else falls back to CFBD ``ppa``.
    """
    metric = func.coalesce(Play.epa, Play.ppa)
    stmt = (
        select(
            Play.offense.label("team"),
            func.count().label("plays"),
            func.round(func.avg(metric), 4).label("avg_ppa"),
        )
        .where(metric.isnot(None), Play.offense.isnot(None))
        .group_by(Play.offense)
        .having(func.count() >= min_plays)
        .order_by(func.avg(metric).desc())
        .limit(limit)
    )
    stmt = _season_filter(stmt, season)
    return [dict(r._mapping) for r in session.execute(stmt)]


def team_scoring_summary(session: Session, season: int, limit: int = 25) -> list[dict[str, Any]]:
    """Points scored per team from game results (home + away), ranked."""
    home = (
        select(
            Game.home_team.label("team"),
            Game.home_points.label("pf"),
            Game.away_points.label("pa"),
        )
        .where(Game.season == season, Game.home_points.isnot(None))
    )
    away = (
        select(
            Game.away_team.label("team"),
            Game.away_points.label("pf"),
            Game.home_points.label("pa"),
        )
        .where(Game.season == season, Game.away_points.isnot(None))
    )
    unioned = home.union_all(away).subquery()
    stmt = (
        select(
            unioned.c.team,
            func.count().label("games"),
            func.sum(unioned.c.pf).label("points_for"),
            func.sum(unioned.c.pa).label("points_against"),
            func.round(func.avg(unioned.c.pf).cast(Integer), 0).label("ppg"),
        )
        .group_by(unioned.c.team)
        .order_by(func.sum(unioned.c.pf).desc())
        .limit(limit)
    )
    return [dict(r._mapping) for r in session.execute(stmt)]
