"""FastAPI app: JSON API + a couple of server-rendered pages.

Run with::

    uvicorn gridiron.api.main:app --reload

Endpoints (JSON):
    GET /api/teams
    GET /api/games?season=2023
    GET /api/games/{game_id}          -> game, drives, plays, box score
    GET /api/analytics/scoring-by-field-position?season=2023
    GET /api/analytics/scoring-types?season=2023
    GET /api/analytics/fg-success?season=2023
    GET /api/analytics/play-type-mix?season=2023
    GET /api/analytics/ppa-leaders?season=2023
    GET /api/analytics/team-scoring?season=2023

Pages (HTML):
    GET /                              -> season/game browser
    GET /games/{game_id}              -> game detail
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gridiron.analytics import charts as ch
from gridiron.analytics import queries as q
from gridiron.analytics.filters import PlayFilter
from gridiron.db.models import (
    Drive,
    Game,
    Play,
    PlayerGameStat,
    Team,
    TeamGameStat,
)
from gridiron.db.session import get_db

_HERE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_HERE / "web" / "templates"))

app = FastAPI(title="Gridiron", description="College football stats & analysis")

_static = _HERE / "web" / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")


@lru_cache
def _plotly_js() -> str:
    from plotly.offline import get_plotlyjs

    return get_plotlyjs()


@app.get("/vendor/plotly.min.js")
def plotly_js() -> Response:
    """Serve the plotly.js bundle shipped inside the `plotly` package.

    Avoids a CDN dependency or a separate build/vendor step — charts work offline.
    """
    return Response(
        content=_plotly_js(),
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# --- JSON API -------------------------------------------------------------

@app.get("/api/teams")
def api_teams(db: Session = Depends(get_db)) -> list[dict]:
    teams = db.execute(select(Team).order_by(Team.school)).scalars().all()
    return [
        {"id": t.id, "school": t.school, "conference": t.conference, "logo": t.logo}
        for t in teams
    ]


@app.get("/api/games")
def api_games(
    season: int = Query(...),
    week: int | None = None,
    team: str | None = None,
    conference: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = _games_query(season, week, team, conference)
    return [_game_summary(g) for g in db.execute(stmt).scalars().all()]


def _games_query(season, week=None, team=None, conference=None):
    stmt = select(Game).where(Game.season == season)
    if week is not None:
        stmt = stmt.where(Game.week == week)
    if team is not None:
        stmt = stmt.where((Game.home_team == team) | (Game.away_team == team))
    if conference is not None:
        stmt = stmt.where(
            (Game.home_conference == conference) | (Game.away_conference == conference)
        )
    return stmt.order_by(Game.start_date, Game.id)


@app.get("/api/games/{game_id}")
def api_game(game_id: int, db: Session = Depends(get_db)) -> dict:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")
    drives = (
        db.execute(select(Drive).where(Drive.game_id == game_id).order_by(Drive.drive_number))
        .scalars()
        .all()
    )
    plays = (
        db.execute(
            select(Play).where(Play.game_id == game_id).order_by(Play.drive_number, Play.play_number)
        )
        .scalars()
        .all()
    )
    player_stats = (
        db.execute(select(PlayerGameStat).where(PlayerGameStat.game_id == game_id))
        .scalars()
        .all()
    )
    team_stats = (
        db.execute(select(TeamGameStat).where(TeamGameStat.game_id == game_id)).scalars().all()
    )
    return {
        "game": _game_summary(game),
        "drives": [
            {
                "id": d.id,
                "offense": d.offense,
                "defense": d.defense,
                "drive_number": d.drive_number,
                "result": d.drive_result,
                "plays": d.plays,
                "yards": d.yards,
                "scoring": d.scoring,
            }
            for d in drives
        ],
        "plays": [_play_dict(p) for p in plays],
        "player_stats": [
            {
                "team": s.team,
                "player": s.player,
                "category": s.category,
                "stat_type": s.stat_type,
                "stat": s.stat,
            }
            for s in player_stats
        ],
        "team_stats": [
            {"team": s.team, "stat_type": s.stat_type, "stat": s.stat} for s in team_stats
        ],
    }


@app.get("/api/analytics/scoring-by-field-position")
def api_scoring_fp(season: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return q.scoring_by_field_position(db, season)


@app.get("/api/analytics/scoring-types")
def api_scoring_types(season: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return q.scoring_type_breakdown(db, season)


@app.get("/api/analytics/fg-success")
def api_fg_success(season: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return q.field_goal_success_by_distance(db, season)


@app.get("/api/analytics/play-type-mix")
def api_play_type_mix(
    season: int | None = None, team: str | None = None, db: Session = Depends(get_db)
) -> list[dict]:
    return q.play_type_mix(db, season, team)


@app.get("/api/analytics/ppa-leaders")
def api_ppa_leaders(season: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return q.ppa_leaders(db, season)


@app.get("/api/analytics/team-scoring")
def api_team_scoring(season: int, db: Session = Depends(get_db)) -> list[dict]:
    return q.team_scoring_summary(db, season)


@app.get("/api/analytics/success-rate")
def api_success_rate(
    season: int | None = None, team: str | None = None, db: Session = Depends(get_db)
) -> list[dict]:
    return q.success_rate(db, season, team)


@app.get("/api/analytics/explosiveness")
def api_explosiveness(
    season: int | None = None, team: str | None = None, db: Session = Depends(get_db)
) -> list[dict]:
    return q.explosiveness(db, season, team)


@app.get("/api/analytics/ppa-by-down")
def api_ppa_by_down(
    season: int | None = None, team: str | None = None, db: Session = Depends(get_db)
) -> list[dict]:
    return q.ppa_by_down(db, season, team)


@app.get("/api/analytics/drive-outcomes")
def api_drive_outcomes(
    season: int, team: str | None = None, db: Session = Depends(get_db)
) -> list[dict]:
    return q.drive_outcomes(db, season, team)


@app.get("/api/analytics/drive-efficiency")
def api_drive_efficiency(team: str, season: int, db: Session = Depends(get_db)) -> dict:
    return q.drive_efficiency(db, team, season)


@app.get("/api/games/{game_id}/lines")
def api_game_lines(game_id: int, db: Session = Depends(get_db)) -> list[dict]:
    return q.game_betting_lines(db, game_id)


@app.get("/api/games/{game_id}/win-probability")
def api_game_wp(game_id: int, db: Session = Depends(get_db)) -> list[dict]:
    return q.game_win_probability(db, game_id)


@app.get("/api/analytics/wp-leaders")
def api_wp_leaders(season: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return q.wp_leaders(db, season)


@app.get("/api/analytics/player-wpa-leaders")
def api_player_wpa_leaders(season: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return q.player_wpa_leaders(db, season, min_plays=1)


@app.get("/api/analytics/ats-record")
def api_ats_record(team: str, season: int, db: Session = Depends(get_db)) -> dict:
    return q.team_ats_record(db, team, season)


@app.get("/api/teams/{team}/trends")
def api_team_trends(team: str, db: Session = Depends(get_db)) -> list[dict]:
    return q.team_season_history(db, team)


@app.get("/api/standings")
def api_standings(season: int, conference: str, db: Session = Depends(get_db)) -> list[dict]:
    return q.conference_standings(db, season, conference)


@app.get("/api/teams/{team}/recruiting")
def api_team_recruiting(team: str, season: int, db: Session = Depends(get_db)) -> dict:
    return {
        "recruiting": q.team_recruiting(db, team, season),
        "transfers": q.team_transfers(db, team, season),
    }


@app.get("/api/coaches")
def api_coaches(
    q_: str | None = Query(default=None, alias="q"), db: Session = Depends(get_db)
) -> list[dict]:
    return q.coach_search(db, q_)


@app.get("/api/coaches/{name}")
def api_coach(name: str, db: Session = Depends(get_db)) -> dict:
    career = q.coach_career(db, name)
    if not career["seasons"]:
        raise HTTPException(status_code=404, detail="coach not found")
    return career


@app.get("/api/analytics/winningest-coaches")
def api_winningest(db: Session = Depends(get_db)) -> list[dict]:
    return q.winningest_coaches(db)


@app.get("/api/teams/{team}")
def api_team(team: str, season: int, db: Session = Depends(get_db)) -> dict:
    summary = q.team_season_summary(db, team, season)
    if summary["games"] == 0 and not q.team_game_log(db, team, season):
        raise HTTPException(status_code=404, detail="team/season not found")
    return {
        "summary": summary,
        "game_log": q.team_game_log(db, team, season),
        "rankings": q.team_rankings_history(db, team, season),
    }


@app.get("/api/players")
def api_players(
    q_: str | None = Query(default=None, alias="q"),
    season: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    return q.player_search(db, q_, season)


@app.get("/api/players/compare")
def api_player_compare(
    a: int, b: int, season: int | None = None, db: Session = Depends(get_db)
) -> dict:
    """Compare two players by id (declared before /{player_id} to avoid capture)."""
    return q.player_compare(db, a, b, season)


@app.get("/api/players/{player_id}")
def api_player(
    player_id: int, season: int | None = None, db: Session = Depends(get_db)
) -> dict:
    profile = q.player_profile(db, player_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="player not found")
    return {
        "profile": profile,
        "season_stats": q.player_season_stats(db, player_id, season),
        "game_log": q.player_game_log(db, player_id, season),
        "wpa": q.player_wpa(db, profile["name"], season) if profile.get("name") else None,
    }


@app.get("/api/compare")
def api_compare(a: str, b: str, season: int, db: Session = Depends(get_db)) -> dict:
    return q.team_compare(db, a, b, season)


# --- HTML pages -----------------------------------------------------------


def _play_filter(
    season: int | None,
    team: str | None = None,
    *,
    week_min: int | None = None,
    week_max: int | None = None,
    home_away: str | None = None,
    conference: str | None = None,
    vs_ranked: bool = False,
    down: int | None = None,
    distance_min: int | None = None,
    distance_max: int | None = None,
) -> PlayFilter:
    return PlayFilter(
        season=season,
        team=team,
        week_min=week_min,
        week_max=week_max,
        home_away=home_away or None,
        conference=conference or None,
        vs_ranked=vs_ranked,
        down=down,
        distance_min=distance_min,
        distance_max=distance_max,
    )

@app.get("/", response_class=HTMLResponse)
def page_index(
    request: Request,
    season: int | None = None,
    week: int | None = None,
    conference: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    seasons = (
        db.execute(select(Game.season).distinct().order_by(Game.season.desc())).scalars().all()
    )
    if season is None and seasons:
        season = seasons[0]
    games = []
    conferences: list[str] = []
    if season is not None:
        games = [
            _game_summary(g)
            for g in db.execute(_games_query(season, week, None, conference)).scalars().all()
        ]
        conferences = sorted(
            {
                c
                for c in db.execute(
                    select(Game.home_conference).where(Game.season == season).distinct()
                ).scalars()
                if c
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "seasons": seasons,
            "season": season,
            "games": games,
            "conferences": conferences,
            "conference": conference or "",
            "week": week,
        },
    )


@app.get("/games/{game_id}", response_class=HTMLResponse)
def page_game(request: Request, game_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")
    plays = (
        db.execute(
            select(Play).where(Play.game_id == game_id).order_by(Play.drive_number, Play.play_number)
        )
        .scalars()
        .all()
    )
    scoring_plays = [p for p in plays if p.scoring]
    wp_rows = q.game_win_probability(db, game_id)
    wp_figure = (
        ch.win_probability_fig(db, game_id, game.home_team, game.away_team) if wp_rows else None
    )
    return templates.TemplateResponse(
        request=request,
        name="game.html",
        context={
            "game": _game_summary(game),
            "plays": [_play_dict(p) for p in plays],
            "scoring_plays": [_play_dict(p) for p in scoring_plays],
            "betting_lines": q.game_betting_lines(db, game_id),
            "wp_figure": wp_figure,
        },
    )


def _current_season(db: Session, season: int | None) -> int | None:
    if season is not None:
        return season
    return db.execute(select(func.max(Game.season))).scalar()


@app.get("/teams", response_class=HTMLResponse)
def page_teams(request: Request, season: int | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    seasons = (
        db.execute(select(Game.season).distinct().order_by(Game.season.desc())).scalars().all()
    )
    season = _current_season(db, season)
    teams = q.list_teams_with_data(db, season) if season is not None else []
    return templates.TemplateResponse(
        request=request,
        name="teams.html",
        context={"seasons": seasons, "season": season, "teams": teams},
    )


@app.get("/standings", response_class=HTMLResponse)
def page_standings(
    request: Request,
    season: int | None = None,
    conference: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    seasons = (
        db.execute(select(Game.season).distinct().order_by(Game.season.desc())).scalars().all()
    )
    season = _current_season(db, season)
    conferences = q.list_conferences(db, season) if season is not None else []
    if conference is None and conferences:
        conference = conferences[0]
    standings = (
        q.conference_standings(db, season, conference)
        if season is not None and conference
        else []
    )
    return templates.TemplateResponse(
        request=request,
        name="standings.html",
        context={
            "seasons": seasons,
            "season": season,
            "conferences": conferences,
            "conference": conference,
            "standings": standings,
        },
    )


@app.get("/coaches", response_class=HTMLResponse)
def page_coaches(
    request: Request,
    q_: str | None = Query(default=None, alias="q"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    results = q.coach_search(db, q_) if q_ else []
    figure = None if q_ else ch.winningest_fig(db)
    winningest = [] if q_ else q.winningest_coaches(db)
    return templates.TemplateResponse(
        request=request,
        name="coaches.html",
        context={"query": q_, "results": results, "figure": figure, "winningest": winningest},
    )


@app.get("/coaches/{name}", response_class=HTMLResponse)
def page_coach(request: Request, name: str, db: Session = Depends(get_db)) -> HTMLResponse:
    career = q.coach_career(db, name)
    if not career["seasons"]:
        raise HTTPException(status_code=404, detail="coach not found")
    return templates.TemplateResponse(
        request=request, name="coach.html", context={"career": career}
    )


@app.get("/leaders", response_class=HTMLResponse)
def page_leaders(request: Request, season: int | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    seasons = (
        db.execute(select(Game.season).distinct().order_by(Game.season.desc())).scalars().all()
    )
    season = _current_season(db, season)
    figures = {}
    ppa = []
    if season is not None:
        figures["wp"] = ch.wp_leaders_fig(db, season)
        figures["player_wpa"] = ch.player_wpa_leaders_fig(db, season)
        ppa = q.ppa_leaders(db, season, min_plays=1)
    return templates.TemplateResponse(
        request=request,
        name="leaders.html",
        context={"seasons": seasons, "season": season, "figures": figures, "ppa_leaders": ppa},
    )


@app.get("/compare", response_class=HTMLResponse)
def page_compare(
    request: Request,
    a: str | None = None,
    b: str | None = None,
    season: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    season = _current_season(db, season)
    teams = q.list_teams_with_data(db, season) if season is not None else []
    comparison = None
    figure = None
    if a and b and season is not None:
        comparison = q.team_compare(db, a, b, season)
        figure = ch.compare_fig(a, b, comparison["a"], comparison["b"])
    return templates.TemplateResponse(
        request=request,
        name="compare.html",
        context={
            "season": season,
            "teams": teams,
            "a": a,
            "b": b,
            "comparison": comparison,
            "figure": figure,
        },
    )


def _resolve_player(db: Session, value: str | None, season: int | None) -> int | None:
    """Interpret a query value as a player id, or resolve a name to the best match."""
    if not value:
        return None
    if value.isdigit():
        return int(value)
    hits = q.player_search(db, value, season)
    return hits[0]["player_id"] if hits else None


@app.get("/players/compare", response_class=HTMLResponse)
def page_player_compare(
    request: Request,
    a: str | None = None,
    b: str | None = None,
    season: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    season = _current_season(db, season)
    a_id = _resolve_player(db, a, season)
    b_id = _resolve_player(db, b, season)
    comparison = None
    figure = None
    if a_id and b_id:
        comparison = q.player_compare(db, a_id, b_id, season)
        figure = ch.player_compare_fig(
            comparison["a"]["profile"]["name"],
            comparison["b"]["profile"]["name"],
            comparison["stats"],
        )
    return templates.TemplateResponse(
        request=request,
        name="player_compare.html",
        context={
            "season": season,
            "a": a or "",
            "b": b or "",
            "comparison": comparison,
            "figure": figure,
        },
    )


@app.get("/teams/{team}", response_class=HTMLResponse)
def page_team(
    request: Request,
    team: str,
    season: int | None = None,
    week_min: int | None = None,
    week_max: int | None = None,
    home_away: str | None = None,
    conference: str | None = None,
    vs_ranked: bool = False,
    down: int | None = None,
    distance_min: int | None = None,
    distance_max: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    season = _current_season(db, season)
    game_log = q.team_game_log(db, team, season) if season is not None else []
    if not game_log:
        raise HTTPException(status_code=404, detail="team/season not found")
    flt = _play_filter(
        season, team, week_min=week_min, week_max=week_max, home_away=home_away,
        conference=conference, vs_ranked=vs_ranked, down=down,
        distance_min=distance_min, distance_max=distance_max,
    )
    extras_set = any([week_min, week_max, home_away, conference, vs_ranked, down, distance_min, distance_max])
    figures = {
        "trend": ch.team_points_trend_fig(db, team, season),
        "field_position": ch.scoring_field_position_fig(db, flt=flt),
        "play_mix": ch.play_type_mix_fig(db, flt=flt),
        "ppa_down": ch.ppa_by_down_fig(db, flt=flt),
        "efficiency": ch.success_explosive_fig(db, season, team, flt=flt),
        "drive_outcomes": ch.drive_outcomes_fig(db, season, team),
    }
    history = q.team_season_history(db, team)
    if len(history) > 1:
        figures["trends"] = ch.team_trends_fig(db, team)
    return templates.TemplateResponse(
        request=request,
        name="team.html",
        context={
            "team": team,
            "season": season,
            "summary": q.team_season_summary(db, team, season),
            "game_log": game_log,
            "rankings": q.team_rankings_history(db, team, season),
            "splits": q.team_splits(db, team, season),
            "drive_efficiency": q.drive_efficiency(db, team, season),
            "ats": q.team_ats_record(db, team, season),
            "recruiting": q.team_recruiting(db, team, season),
            "transfers": q.team_transfers(db, team, season),
            "coaches": q.team_coaches(db, team, season),
            "figures": figures,
            "filters": {
                "week_min": week_min, "week_max": week_max, "home_away": home_away or "",
                "conference": conference or "", "vs_ranked": vs_ranked, "down": down or "",
                "distance_min": distance_min, "distance_max": distance_max,
            },
            "filters_active": extras_set,
        },
    )


@app.get("/players", response_class=HTMLResponse)
def page_players(
    request: Request,
    q_: str | None = Query(default=None, alias="q"),
    season: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    season = _current_season(db, season)
    results = q.player_search(db, q_, season) if q_ else []
    return templates.TemplateResponse(
        request=request,
        name="players.html",
        context={"season": season, "query": q_, "results": results},
    )


@app.get("/players/{player_id}", response_class=HTMLResponse)
def page_player(
    request: Request, player_id: int, season: int | None = None, db: Session = Depends(get_db)
) -> HTMLResponse:
    profile = q.player_profile(db, player_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="player not found")
    return templates.TemplateResponse(
        request=request,
        name="player.html",
        context={
            "profile": profile,
            "season": season,
            "season_stats": q.player_season_stats(db, player_id, season),
            "game_log": q.player_game_log(db, player_id, season),
            "wpa": q.player_wpa(db, profile["name"], season) if profile.get("name") else None,
        },
    )


# --- helpers --------------------------------------------------------------

def _game_summary(g: Game) -> dict:
    return {
        "id": g.id,
        "season": g.season,
        "week": g.week,
        "season_type": g.season_type,
        "start_date": g.start_date.isoformat() if g.start_date else None,
        "home_team": g.home_team,
        "home_points": g.home_points,
        "away_team": g.away_team,
        "away_points": g.away_points,
        "neutral_site": g.neutral_site,
        "excitement_index": g.excitement_index,
    }


def _play_dict(p: Play) -> dict:
    return {
        "id": p.id,
        "drive_number": p.drive_number,
        "play_number": p.play_number,
        "offense": p.offense,
        "defense": p.defense,
        "period": p.period,
        "clock": f"{p.clock_minutes or 0:02d}:{p.clock_seconds or 0:02d}",
        "down": p.down,
        "distance": p.distance,
        "yard_line": p.yard_line,
        "yards_to_goal": p.yards_to_goal,
        "yards_gained": p.yards_gained,
        "play_type": p.play_type,
        "scoring": p.scoring,
        "points_scored": p.points_scored,
        "play_text": p.play_text,
    }
