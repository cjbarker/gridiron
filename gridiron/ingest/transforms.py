"""Pure functions that normalize raw source dicts into ORM model instances.

CFBD serves camelCase JSON over REST (``homeTeam``) while its Python client and
the parquet dumps use snake_case (``home_team``). Every accessor here tries both
spellings via :func:`pick`, so the same transforms work against the live API,
the client, and JSON fixtures. No DB or network here — trivially unit-testable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gridiron.db.models import (
    Drive,
    Game,
    Play,
    Player,
    PlayerGameStat,
    Ranking,
    Team,
    TeamGameStat,
    Venue,
)


def pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present, non-None value among ``keys`` (camel or snake)."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# --- Scoring point values by play type ------------------------------------

def points_for_play_type(play_type: str | None) -> int:
    """Point value a play type is worth (0 for non-scoring plays).

    Mirrors CFBD play granularity: a touchdown play is 6; the ensuing PAT is a
    separate ``Extra Point Good`` (1) or ``Two Point`` (2) play.
    """
    if not play_type:
        return 0
    pt = play_type.lower()
    if "touchdown" in pt:
        return 6
    if "field goal good" in pt:
        return 3
    if "safety" in pt:
        return 2
    if "two point" in pt or "2pt" in pt or "defensive 2pt" in pt:
        return 2
    if "extra point good" in pt:
        return 1
    return 0


# --- Entity transforms ----------------------------------------------------

def to_team(d: dict[str, Any]) -> Team:
    logos = pick(d, "logos", default=None)
    logo = logos[0] if isinstance(logos, list) and logos else None
    return Team(
        id=_int(pick(d, "id")),
        school=pick(d, "school", "team", default="Unknown"),
        mascot=pick(d, "mascot"),
        abbreviation=pick(d, "abbreviation"),
        conference=pick(d, "conference"),
        division=pick(d, "division", "classification"),
        classification=pick(d, "classification"),
        color=pick(d, "color"),
        alt_color=pick(d, "alternateColor", "alt_color"),
        logo=logo,
    )


def to_venue(d: dict[str, Any]) -> Venue:
    loc = pick(d, "location", default={}) or {}
    return Venue(
        id=_int(pick(d, "id")),
        name=pick(d, "name"),
        city=pick(d, "city") or loc.get("city"),
        state=pick(d, "state") or loc.get("state"),
        capacity=_int(pick(d, "capacity")),
        grass=pick(d, "grass"),
        dome=pick(d, "dome"),
    )


def to_game(d: dict[str, Any]) -> Game:
    return Game(
        id=_int(pick(d, "id")),
        season=_int(pick(d, "season", "year")),
        week=_int(pick(d, "week")),
        season_type=pick(d, "seasonType", "season_type"),
        start_date=_dt(pick(d, "startDate", "start_date")),
        completed=pick(d, "completed"),
        neutral_site=pick(d, "neutralSite", "neutral_site"),
        conference_game=pick(d, "conferenceGame", "conference_game"),
        attendance=_int(pick(d, "attendance")),
        venue_id=_int(pick(d, "venueId", "venue_id")),
        home_id=_int(pick(d, "homeId", "home_id")),
        home_team=pick(d, "homeTeam", "home_team"),
        home_conference=pick(d, "homeConference", "home_conference"),
        home_points=_int(pick(d, "homePoints", "home_points")),
        away_id=_int(pick(d, "awayId", "away_id")),
        away_team=pick(d, "awayTeam", "away_team"),
        away_conference=pick(d, "awayConference", "away_conference"),
        away_points=_int(pick(d, "awayPoints", "away_points")),
        excitement_index=_float(pick(d, "excitementIndex", "excitement_index")),
    )


def to_drive(d: dict[str, Any]) -> Drive:
    return Drive(
        id=_int(pick(d, "id")),
        game_id=_int(pick(d, "gameId", "game_id")),
        offense=pick(d, "offense"),
        defense=pick(d, "defense"),
        drive_number=_int(pick(d, "driveNumber", "drive_number")),
        scoring=pick(d, "scoring"),
        start_period=_int(pick(d, "startPeriod", "start_period")),
        start_yardline=_int(pick(d, "startYardline", "start_yardline")),
        start_yards_to_goal=_int(pick(d, "startYardsToGoal", "start_yards_to_goal")),
        end_period=_int(pick(d, "endPeriod", "end_period")),
        end_yardline=_int(pick(d, "endYardline", "end_yardline")),
        end_yards_to_goal=_int(pick(d, "endYardsToGoal", "end_yards_to_goal")),
        plays=_int(pick(d, "plays")),
        yards=_int(pick(d, "yards")),
        drive_result=pick(d, "driveResult", "drive_result"),
    )


def to_play(d: dict[str, Any], season: int | None = None) -> Play:
    """Normalize a play from either the CFBD REST API or a cfbfastR parquet row.

    The two sources name columns differently (e.g. CFBD ``offense`` /
    ``yardLine`` vs cfbfastR ``pos_team`` / ``yards_to_goal`` and a flattened
    ``clock.minutes``), so every accessor lists both spellings.
    """
    play_type = pick(d, "playType", "play_type")
    scoring = pick(d, "scoring", default=False)
    points = points_for_play_type(play_type)
    return Play(
        id=_int(pick(d, "id", "id_play")),
        game_id=_int(pick(d, "gameId", "game_id")),
        season=season if season is not None else _int(pick(d, "season", "year")),
        drive_id=_int(pick(d, "driveId", "drive_id")),
        drive_number=_int(pick(d, "driveNumber", "drive_number")),
        play_number=_int(pick(d, "playNumber", "play_number", "game_play_number")),
        offense=pick(d, "offense", "pos_team", "offense_play"),
        defense=pick(d, "defense", "def_pos_team", "defense_play"),
        offense_score=_int(pick(d, "offenseScore", "offense_score", "pos_team_score")),
        defense_score=_int(pick(d, "defenseScore", "defense_score", "def_pos_team_score")),
        period=_int(pick(d, "period")),
        clock_minutes=_int(_clock_part(d, "minutes")),
        clock_seconds=_int(_clock_part(d, "seconds")),
        down=_int(pick(d, "down")),
        distance=_int(pick(d, "distance")),
        yard_line=_int(pick(d, "yardLine", "yard_line", "yardline")),
        yards_to_goal=_int(pick(d, "yardsToGoal", "yards_to_goal")),
        yards_gained=_int(pick(d, "yardsGained", "yards_gained")),
        play_type=play_type,
        play_text=pick(d, "playText", "play_text"),
        scoring=bool(scoring),
        points_scored=points if (scoring or points) else 0,
        ppa=_float(pick(d, "ppa")),
        epa=_float(pick(d, "EPA", "epa")),
        wp=_float(pick(d, "wp", "wp_before", "wpa", "winProbability")),
    )


def _clock_part(d: dict[str, Any], part: str) -> Any:
    """Clock is nested (CFBD ``clock: {minutes, seconds}``) or flattened with a
    dotted key (cfbfastR ``clock.minutes``). Handle both."""
    clock = d.get("clock")
    if isinstance(clock, dict) and clock.get(part) is not None:
        return clock[part]
    return pick(d, f"clock.{part}", f"clock_{part}")


def to_game_stub(year: int, game_id: int, plays: list[dict[str, Any]]) -> Game:
    """Synthesize a minimal Game from a group of parquet play rows.

    Used by the offline backfill (no CFBD API): teams come from home/away (or the
    possession teams), and final points are the max score seen for each side.
    A later API ingest ``merge``es richer data onto the same game id.
    """
    home = away = None
    week = None
    home_pts = away_pts = 0
    for p in plays:
        week = week or _int(pick(p, "week"))
        home = home or pick(p, "home", "home_team")
        away = away or pick(p, "away", "away_team")
        off = pick(p, "offense", "pos_team", "offense_play")
        os_ = _int(pick(p, "offenseScore", "offense_score", "pos_team_score")) or 0
        ds_ = _int(pick(p, "defenseScore", "defense_score", "def_pos_team_score")) or 0
        # Attribute each side's running score to home/away.
        if off and home and off == home:
            home_pts, away_pts = max(home_pts, os_), max(away_pts, ds_)
        elif off and away and off == away:
            away_pts, home_pts = max(away_pts, os_), max(home_pts, ds_)
    return Game(
        id=game_id,
        season=year,
        week=week,
        season_type="regular",
        home_team=home,
        away_team=away,
        home_points=home_pts,
        away_points=away_pts,
    )


def to_player(d: dict[str, Any]) -> Player:
    return Player(
        id=_int(pick(d, "id")),
        first_name=pick(d, "firstName", "first_name"),
        last_name=pick(d, "lastName", "last_name"),
        team=pick(d, "team"),
        position=pick(d, "position"),
        height=_int(pick(d, "height")),
        weight=_int(pick(d, "weight")),
        jersey=_int(pick(d, "jersey")),
        year=_int(pick(d, "year")),
        home_city=pick(d, "homeCity", "home_city"),
        home_state=pick(d, "homeState", "home_state"),
    )


# --- Box-score flatteners (nested -> long rows) ---------------------------

def flatten_player_game_stats(
    game_id: int, season: int | None, record: dict[str, Any]
) -> list[PlayerGameStat]:
    """CFBD /games/players nests team -> categories -> types -> athletes.

    Returns one :class:`PlayerGameStat` per (player, category, stat_type).
    """
    rows: list[PlayerGameStat] = []
    for team_block in pick(record, "teams", default=[]) or []:
        team = pick(team_block, "team", "school")
        for cat in pick(team_block, "categories", default=[]) or []:
            category = pick(cat, "name", "category")
            for typ in pick(cat, "types", default=[]) or []:
                stat_type = pick(typ, "name", "type")
                for ath in pick(typ, "athletes", default=[]) or []:
                    rows.append(
                        PlayerGameStat(
                            game_id=game_id,
                            season=season,
                            team=team,
                            player_id=_int(pick(ath, "id")),
                            player=pick(ath, "name"),
                            category=str(category),
                            stat_type=str(stat_type),
                            stat=_stat_str(pick(ath, "stat")),
                        )
                    )
    return rows


def flatten_team_game_stats(
    game_id: int, season: int | None, record: dict[str, Any]
) -> list[TeamGameStat]:
    """CFBD /games/teams nests team -> stats[]. Returns one row per (team, stat)."""
    rows: list[TeamGameStat] = []
    for team_block in pick(record, "teams", default=[]) or []:
        team = pick(team_block, "team", "school")
        for stat in pick(team_block, "stats", default=[]) or []:
            rows.append(
                TeamGameStat(
                    game_id=game_id,
                    season=season,
                    team=str(team),
                    stat_type=str(pick(stat, "category", "name")),
                    stat=_stat_str(pick(stat, "stat")),
                )
            )
    return rows


def flatten_rankings(year: int, records: list[dict[str, Any]]) -> list[Ranking]:
    """CFBD /rankings nests week -> polls -> ranks[]. Returns flat Ranking rows."""
    rows: list[Ranking] = []
    for wk in records:
        week = _int(pick(wk, "week"))
        season_type = pick(wk, "seasonType", "season_type")
        for poll in pick(wk, "polls", default=[]) or []:
            poll_name = pick(poll, "poll", "name")
            for rank in pick(poll, "ranks", default=[]) or []:
                rows.append(
                    Ranking(
                        season=year,
                        week=week,
                        season_type=season_type,
                        poll=str(poll_name),
                        rank=_int(pick(rank, "rank")),
                        team=str(pick(rank, "school", "team")),
                        conference=pick(rank, "conference"),
                        points=_int(pick(rank, "points")),
                        first_place_votes=_int(pick(rank, "firstPlaceVotes", "first_place_votes")),
                    )
                )
    return rows


def _stat_str(v: Any) -> str | None:
    return None if v is None else str(v)
