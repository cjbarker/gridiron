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
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(Game).where(Game.season == season)
    if week is not None:
        stmt = stmt.where(Game.week == week)
    if team is not None:
        stmt = stmt.where((Game.home_team == team) | (Game.away_team == team))
    stmt = stmt.order_by(Game.start_date, Game.id)
    return [_game_summary(g) for g in db.execute(stmt).scalars().all()]


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
    }


# --- HTML pages -----------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def page_index(request: Request, season: int | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    seasons = (
        db.execute(select(Game.season).distinct().order_by(Game.season.desc())).scalars().all()
    )
    if season is None and seasons:
        season = seasons[0]
    games = []
    if season is not None:
        games = [
            _game_summary(g)
            for g in db.execute(
                select(Game).where(Game.season == season).order_by(Game.start_date, Game.id)
            )
            .scalars()
            .all()
        ]
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"seasons": seasons, "season": season, "games": games},
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
    return templates.TemplateResponse(
        request=request,
        name="game.html",
        context={
            "game": _game_summary(game),
            "plays": [_play_dict(p) for p in plays],
            "scoring_plays": [_play_dict(p) for p in scoring_plays],
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


@app.get("/teams/{team}", response_class=HTMLResponse)
def page_team(
    request: Request, team: str, season: int | None = None, db: Session = Depends(get_db)
) -> HTMLResponse:
    season = _current_season(db, season)
    game_log = q.team_game_log(db, team, season) if season is not None else []
    if not game_log:
        raise HTTPException(status_code=404, detail="team/season not found")
    figures = {
        "trend": ch.team_points_trend_fig(db, team, season),
        "field_position": ch.scoring_field_position_fig(db, season, team),
        "play_mix": ch.play_type_mix_fig(db, season, team),
        "ppa_down": ch.ppa_by_down_fig(db, season, team),
        "efficiency": ch.success_explosive_fig(db, season, team),
    }
    return templates.TemplateResponse(
        request=request,
        name="team.html",
        context={
            "team": team,
            "season": season,
            "summary": q.team_season_summary(db, team, season),
            "game_log": game_log,
            "rankings": q.team_rankings_history(db, team, season),
            "figures": figures,
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
