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

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

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
