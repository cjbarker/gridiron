"""Derived statistical queries.

These functions turn the raw ``plays``/``games`` tables into the kinds of
analysis the site is about — where on the field points come from, how scoring
breaks down by type, field-goal success by distance, play-type mix, and
efficiency (PPA/EPA) leaders. Each returns plain dicts, ready for JSON or a
template. All are read-only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, and_, case, func, or_, select
from sqlalchemy.orm import Session

from gridiron.analytics.filters import PlayFilter, apply_play_filter
from gridiron.db.models import Game, Play, Player, PlayerGameStat, Ranking

# Field-position buckets by yards-to-goal (distance to opponent end zone).
_FP_BUCKETS = [
    (0, 10, "Red zone (0-10)"),
    (11, 20, "11-20"),
    (21, 40, "21-40"),
    (41, 60, "41-60"),
    (61, 80, "61-80"),
    (81, 100, "Backed up (81-100)"),
]


def _scope(stmt, season: int | None, team: str | None, flt: PlayFilter | None):
    """Scope a Play query by an explicit :class:`PlayFilter`, or by season/team.

    When ``flt`` is given it fully specifies the scope; otherwise a simple
    ``PlayFilter(season, team)`` is applied — so existing callers are unchanged.
    """
    if flt is None:
        flt = PlayFilter(season=season, team=team)
    return apply_play_filter(stmt, flt)


def scoring_by_field_position(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    flt: PlayFilter | None = None,
) -> list[dict[str, Any]]:
    """Count and total points of scoring plays, bucketed by field position.

    Answers the core question: *from what yardage on the field are points scored?*
    Pass ``team`` to scope to one offense (used by team pages).
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
    stmt = _scope(stmt, season, team, flt)
    order = {label: i for i, (_, _, label) in enumerate(_FP_BUCKETS)}
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    rows.sort(key=lambda r: order.get(r["bucket"], 99))
    return rows


def scoring_type_breakdown(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    flt: PlayFilter | None = None,
) -> list[dict[str, Any]]:
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
    stmt = _scope(stmt, season, team, flt)
    return [dict(r._mapping) for r in session.execute(stmt)]


def field_goal_success_by_distance(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    flt: PlayFilter | None = None,
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
    stmt = _scope(stmt, season, team, flt)
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
    session: Session,
    season: int | None = None,
    team: str | None = None,
    limit: int = 20,
    flt: PlayFilter | None = None,
) -> list[dict[str, Any]]:
    """Most common play types (optionally for a single offense)."""
    stmt = (
        select(Play.play_type, func.count().label("plays"))
        .group_by(Play.play_type)
        .order_by(func.count().desc())
        .limit(limit)
    )
    stmt = _scope(stmt, season, team, flt)
    return [dict(r._mapping) for r in session.execute(stmt)]


def ppa_leaders(
    session: Session,
    season: int | None = None,
    min_plays: int = 200,
    limit: int = 25,
    flt: PlayFilter | None = None,
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
    stmt = _scope(stmt, season, None, flt)
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


# --- Team dossier ---------------------------------------------------------

def list_teams_with_data(session: Session, season: int) -> list[dict[str, Any]]:
    """Teams that appear in a season's schedule (home or away), de-duplicated."""
    home = select(Game.home_team.label("team")).where(Game.season == season)
    away = select(Game.away_team.label("team")).where(Game.season == season)
    sub = home.union(away).subquery()
    stmt = select(sub.c.team).where(sub.c.team.isnot(None)).order_by(sub.c.team)
    return [{"team": r[0]} for r in session.execute(stmt)]


def team_game_log(session: Session, team: str, season: int) -> list[dict[str, Any]]:
    """Per-game log for a team: opponent, home/away, score, and result."""
    stmt = (
        select(Game)
        .where(Game.season == season, or_(Game.home_team == team, Game.away_team == team))
        .order_by(Game.week, Game.start_date, Game.id)
    )
    out: list[dict[str, Any]] = []
    for g in session.execute(stmt).scalars():
        is_home = g.home_team == team
        pf = g.home_points if is_home else g.away_points
        pa = g.away_points if is_home else g.home_points
        result = None
        if pf is not None and pa is not None:
            result = "W" if pf > pa else "L" if pf < pa else "T"
        out.append(
            {
                "game_id": g.id,
                "week": g.week,
                "opponent": g.away_team if is_home else g.home_team,
                "home_away": "home" if is_home else "away",
                "points_for": pf,
                "points_against": pa,
                "result": result,
            }
        )
    return out


def team_season_summary(session: Session, team: str, season: int) -> dict[str, Any]:
    """Record (W-L-T), points for/against, and per-game scoring for a team."""
    log = team_game_log(session, team, season)
    played = [g for g in log if g["result"] is not None]
    wins = sum(1 for g in played if g["result"] == "W")
    losses = sum(1 for g in played if g["result"] == "L")
    ties = sum(1 for g in played if g["result"] == "T")
    pf = sum(g["points_for"] for g in played)
    pa = sum(g["points_against"] for g in played)
    n = len(played)
    return {
        "team": team,
        "season": season,
        "games": n,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
        "points_for": pf,
        "points_against": pa,
        "ppg": round(pf / n, 1) if n else None,
        "papg": round(pa / n, 1) if n else None,
    }


def team_rankings_history(session: Session, team: str, season: int) -> list[dict[str, Any]]:
    """A team's poll ranking by week (AP Top 25 etc.)."""
    stmt = (
        select(Ranking.week, Ranking.poll, Ranking.rank)
        .where(Ranking.season == season, Ranking.team == team)
        .order_by(Ranking.poll, Ranking.week)
    )
    return [dict(r._mapping) for r in session.execute(stmt)]


# --- Player views ---------------------------------------------------------

def player_search(
    session: Session, q: str | None = None, season: int | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Find players that have box-score data, by (partial) name."""
    stmt = select(
        PlayerGameStat.player_id, PlayerGameStat.player, PlayerGameStat.team
    ).distinct()
    if q:
        stmt = stmt.where(PlayerGameStat.player.ilike(f"%{q}%"))
    if season is not None:
        stmt = stmt.where(PlayerGameStat.season == season)
    stmt = stmt.where(PlayerGameStat.player_id.isnot(None)).order_by(PlayerGameStat.player).limit(limit)
    return [
        {"player_id": r[0], "player": r[1], "team": r[2]} for r in session.execute(stmt)
    ]


def player_profile(session: Session, player_id: int) -> dict[str, Any] | None:
    """Bio/roster info from the players table, if a roster has been ingested."""
    p = session.get(Player, player_id)
    if p is None:
        # Fall back to whatever the box scores know.
        row = session.execute(
            select(PlayerGameStat.player, PlayerGameStat.team)
            .where(PlayerGameStat.player_id == player_id)
            .limit(1)
        ).first()
        if row is None:
            return None
        return {"id": player_id, "name": row[0], "team": row[1]}
    name = " ".join(x for x in [p.first_name, p.last_name] if x)
    return {
        "id": p.id,
        "name": name,
        "team": p.team,
        "position": p.position,
        "jersey": p.jersey,
        "height": p.height,
        "weight": p.weight,
        "year": p.year,
        "hometown": ", ".join(x for x in [p.home_city, p.home_state] if x) or None,
    }


def player_season_stats(
    session: Session, player_id: int, season: int | None = None
) -> list[dict[str, Any]]:
    """Aggregate a player's box-score stats across games.

    Values are stored as strings (EAV), so we sum numeric ones (YDS, TD, ...) and
    otherwise report the number of games the stat appears in.
    """
    stmt = select(
        PlayerGameStat.category, PlayerGameStat.stat_type, PlayerGameStat.stat
    ).where(PlayerGameStat.player_id == player_id)
    if season is not None:
        stmt = stmt.where(PlayerGameStat.season == season)

    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for category, stat_type, stat in session.execute(stmt):
        key = (category, stat_type)
        entry = agg.setdefault(
            key, {"category": category, "stat_type": stat_type, "games": 0, "total": 0.0, "_numeric": True}
        )
        entry["games"] += 1
        try:
            entry["total"] += float(stat)
        except (TypeError, ValueError):
            entry["_numeric"] = False

    out = []
    for entry in agg.values():
        numeric = entry.pop("_numeric")
        total = entry.pop("total")
        entry["total"] = round(total, 1) if numeric else None
        out.append(entry)
    out.sort(key=lambda e: (e["category"], e["stat_type"]))
    return out


def player_game_log(
    session: Session, player_id: int, season: int | None = None
) -> list[dict[str, Any]]:
    """Per-game stat lines for a player, with game context."""
    stmt = (
        select(
            PlayerGameStat.game_id,
            Game.week,
            PlayerGameStat.team,
            PlayerGameStat.category,
            PlayerGameStat.stat_type,
            PlayerGameStat.stat,
        )
        .join(Game, Game.id == PlayerGameStat.game_id)
        .where(PlayerGameStat.player_id == player_id)
        .order_by(Game.week, PlayerGameStat.game_id, PlayerGameStat.category)
    )
    if season is not None:
        stmt = stmt.where(PlayerGameStat.season == season)

    games: dict[int, dict[str, Any]] = {}
    for game_id, week, team, category, stat_type, stat in session.execute(stmt):
        g = games.setdefault(
            game_id, {"game_id": game_id, "week": week, "team": team, "stats": {}}
        )
        g["stats"][f"{category} {stat_type}"] = stat
    return list(games.values())


# --- Advanced efficiency metrics -----------------------------------------

_SUCCESS = case(
    (and_(Play.down == 1, Play.yards_gained >= 0.5 * Play.distance), 1),
    (and_(Play.down == 2, Play.yards_gained >= 0.7 * Play.distance), 1),
    (and_(Play.down.in_([3, 4]), Play.yards_gained >= Play.distance), 1),
    else_=0,
)


def _run_pass_scope(stmt):
    """Restrict to scrimmage plays with a valid down/distance/yardage."""
    return stmt.where(
        Play.down.in_([1, 2, 3, 4]),
        Play.distance.isnot(None),
        Play.yards_gained.isnot(None),
    )


def success_rate(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    min_plays: int = 1,
    flt: PlayFilter | None = None,
) -> list[dict[str, Any]]:
    """Offensive success rate per team (see `_SUCCESS` for the down thresholds)."""
    stmt = _run_pass_scope(
        select(
            Play.offense.label("team"),
            func.count().label("plays"),
            func.round(func.avg(_SUCCESS), 4).label("success_rate"),
        )
    ).group_by(Play.offense).having(func.count() >= min_plays).order_by(func.avg(_SUCCESS).desc())
    stmt = _scope(stmt, season, team, flt)
    return [dict(r._mapping) for r in session.execute(stmt)]


def explosiveness(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    min_plays: int = 1,
    flt: PlayFilter | None = None,
) -> list[dict[str, Any]]:
    """Share of a team's plays gaining 15+ yards (an 'explosive' play)."""
    explosive = case((Play.yards_gained >= 15, 1), else_=0)
    stmt = (
        select(
            Play.offense.label("team"),
            func.count().label("plays"),
            func.round(func.avg(explosive), 4).label("explosive_rate"),
        )
        .where(Play.yards_gained.isnot(None), Play.offense.isnot(None))
        .group_by(Play.offense)
        .having(func.count() >= min_plays)
        .order_by(func.avg(explosive).desc())
    )
    stmt = _scope(stmt, season, team, flt)
    return [dict(r._mapping) for r in session.execute(stmt)]


def ppa_by_down(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    flt: PlayFilter | None = None,
) -> list[dict[str, Any]]:
    """Average PPA/EPA grouped by down (uses `epa` when present, else `ppa`)."""
    metric = func.coalesce(Play.epa, Play.ppa)
    stmt = (
        select(
            Play.down,
            func.count().label("plays"),
            func.round(func.avg(metric), 4).label("avg_ppa"),
        )
        .where(metric.isnot(None), Play.down.in_([1, 2, 3, 4]))
        .group_by(Play.down)
        .order_by(Play.down)
    )
    stmt = _scope(stmt, season, team, flt)
    return [dict(r._mapping) for r in session.execute(stmt)]


# --- Splits & matchups ----------------------------------------------------

def _record_of(games: list[dict[str, Any]]) -> dict[str, Any]:
    played = [g for g in games if g["result"] is not None]
    wins = sum(1 for g in played if g["result"] == "W")
    losses = sum(1 for g in played if g["result"] == "L")
    pf = sum(g["points_for"] for g in played)
    pa = sum(g["points_against"] for g in played)
    n = len(played)
    return {
        "games": n,
        "record": f"{wins}-{losses}",
        "points_for": pf,
        "points_against": pa,
        "ppg": round(pf / n, 1) if n else None,
    }


def team_splits(session: Session, team: str, season: int) -> dict[str, Any]:
    """Home/away sub-records + scoring by quarter for a team."""
    log = team_game_log(session, team, season)
    quarter = (
        select(
            Play.period,
            func.count().label("scoring_plays"),
            func.coalesce(func.sum(Play.points_scored), 0).label("points"),
        )
        .where(Play.scoring.is_(True))
        .group_by(Play.period)
        .order_by(Play.period)
    )
    quarter = _scope(quarter, season, team, None)
    return {
        "home": _record_of([g for g in log if g["home_away"] == "home"]),
        "away": _record_of([g for g in log if g["home_away"] == "away"]),
        "by_quarter": [dict(r._mapping) for r in session.execute(quarter)],
    }


def _team_metrics(session: Session, team: str, season: int) -> dict[str, Any]:
    """A team's headline metrics for a season (for comparison views)."""
    flt = PlayFilter(season=season, team=team)
    sr = success_rate(session, flt=flt)
    ex = explosiveness(session, flt=flt)
    ppa = ppa_leaders(session, min_plays=1, flt=flt)
    return {
        "summary": team_season_summary(session, team, season),
        "success_rate": sr[0]["success_rate"] if sr else None,
        "explosive_rate": ex[0]["explosive_rate"] if ex else None,
        "avg_ppa": ppa[0]["avg_ppa"] if ppa else None,
        "scoring_by_field_position": scoring_by_field_position(session, flt=flt),
    }


def team_compare(session: Session, a: str, b: str, season: int) -> dict[str, Any]:
    """Two teams side by side for a season, plus their head-to-head game(s)."""
    h2h_stmt = (
        select(Game)
        .where(
            Game.season == season,
            or_(
                and_(Game.home_team == a, Game.away_team == b),
                and_(Game.home_team == b, Game.away_team == a),
            ),
        )
        .order_by(Game.week, Game.id)
    )
    head_to_head = [
        {
            "game_id": g.id,
            "week": g.week,
            "home_team": g.home_team,
            "home_points": g.home_points,
            "away_team": g.away_team,
            "away_points": g.away_points,
        }
        for g in session.execute(h2h_stmt).scalars()
    ]
    return {
        "season": season,
        "a": _team_metrics(session, a, season),
        "b": _team_metrics(session, b, season),
        "head_to_head": head_to_head,
    }


def player_compare(
    session: Session, a_id: int, b_id: int, season: int | None = None
) -> dict[str, Any]:
    """Two players' season stat lines aligned side by side.

    Rows cover every (category, stat_type) either player recorded; numeric totals
    come from :func:`player_season_stats`.
    """
    a_stats = {
        (r["category"], r["stat_type"]): r for r in player_season_stats(session, a_id, season)
    }
    b_stats = {
        (r["category"], r["stat_type"]): r for r in player_season_stats(session, b_id, season)
    }
    keys = sorted(set(a_stats) | set(b_stats))
    rows = [
        {
            "category": c,
            "stat_type": t,
            "a": a_stats.get((c, t), {}).get("total"),
            "b": b_stats.get((c, t), {}).get("total"),
        }
        for (c, t) in keys
    ]
    return {
        "season": season,
        "a": {"profile": player_profile(session, a_id)},
        "b": {"profile": player_profile(session, b_id)},
        "stats": rows,
    }
