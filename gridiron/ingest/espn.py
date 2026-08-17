"""ESPN data source — a key-free alternative to CFBD for the ingest core.

ESPN's public site API needs no key and is forgiving on rate limits, but serves
JSON in its own shape. This module **reshapes ESPN responses into the exact
CFBD-field-named dicts** that :mod:`gridiron.ingest.transforms` already normalizes,
so the pipeline, transforms, and DB are untouched.

CFBD game/team IDs *are* ESPN IDs (verified against the fixtures), so ESPN-ingested
games/teams land on the same primary keys the DB already uses and the cfbfastR
parquet's ``game_id`` joins cleanly.

Two classes are exported:

* :class:`ESPNSource` — the core (teams, venues, games, drives, plays, box scores,
  rankings, rosters). Rosters are current-season only (ESPN's roster endpoint does
  not serve historical seasons); plays work but the recommended bulk path stays
  ``--plays-source parquet``.
* :class:`HybridSource` — ESPN core + CFBD for the four categories ESPN can't serve
  (recruiting, transfers, coaches, betting lines). The CFBD client is built lazily,
  so core-only work needs no key.
"""

from __future__ import annotations

from typing import Any

from gridiron.ingest.pipeline import current_season
from gridiron.ingest.sources import POSTSEASON, REGULAR

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
# The site /teams endpoint can't filter to FBS; the core API's FBS group can.
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"

# CFBD season-type name -> ESPN numeric seasontype.
_SEASONTYPE = {REGULAR: 2, POSTSEASON: 3}
# How far to probe weeks before giving up (a couple of empty weeks ends the sweep).
_MAX_WEEK = {REGULAR: 16, POSTSEASON: 5}


# --- Small parsing helpers ------------------------------------------------

def _parse_clock(clock: dict | None) -> dict:
    """ESPN clock is ``{'displayValue': 'MM:SS'}``; CFBD wants ``{minutes, seconds}``."""
    dv = (clock or {}).get("displayValue")
    if isinstance(dv, str) and ":" in dv:
        mm, _, ss = dv.partition(":")
        return {"minutes": _to_int(mm), "seconds": _to_int(ss)}
    return {}


def _to_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _competitors(event: dict) -> tuple[dict, dict] | None:
    """Return ``(home, away)`` competitor dicts, or None if not a two-team event."""
    comp = (event.get("competitions") or [{}])[0]
    by_side = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    if "home" in by_side and "away" in by_side:
        return by_side["home"], by_side["away"]
    return None


# --- Pure reshapers: ESPN dict -> CFBD-shaped dict ------------------------

def reshape_team(entry: dict) -> dict:
    """One ``teams`` list entry (``{"team": {...}}``) -> CFBD ``/teams`` dict."""
    t = entry.get("team", entry)
    logos = [logo.get("href") for logo in t.get("logos", []) if logo.get("href")]
    return {
        "id": t.get("id"),
        "school": t.get("location"),
        "mascot": t.get("name"),
        "abbreviation": t.get("abbreviation"),
        "conference": t.get("conference"),
        "color": t.get("color"),
        "alternateColor": t.get("alternateColor"),
        "logos": logos,
    }


def reshape_venue(venue: dict) -> dict:
    """Scoreboard ``competition.venue`` -> CFBD ``/venues`` dict."""
    addr = venue.get("address") or {}
    return {
        "id": venue.get("id"),
        "name": venue.get("fullName"),
        "city": addr.get("city"),
        "state": addr.get("state"),
        "dome": venue.get("indoor"),
    }


def reshape_game(event: dict, season_type: str) -> dict | None:
    """Scoreboard ``event`` -> CFBD ``/games`` dict (None if not two-team)."""
    sides = _competitors(event)
    if sides is None:
        return None
    home, away = sides
    comp = (event.get("competitions") or [{}])[0]

    def _team_name(c: dict) -> str | None:
        return (c.get("team") or {}).get("location") or (c.get("team") or {}).get("displayName")

    def _team_id(c: dict) -> Any:
        return (c.get("team") or {}).get("id") or c.get("id")

    return {
        "id": event.get("id"),
        "season": (event.get("season") or {}).get("year"),
        "week": (event.get("week") or {}).get("number"),
        "seasonType": season_type,
        "startDate": comp.get("date") or event.get("date"),
        "completed": ((event.get("status") or {}).get("type") or {}).get("completed"),
        "neutralSite": comp.get("neutralSite"),
        "conferenceGame": comp.get("conferenceCompetition"),
        "attendance": comp.get("attendance"),
        "venueId": (comp.get("venue") or {}).get("id"),
        "homeId": _team_id(home),
        "homeTeam": _team_name(home),
        "homePoints": home.get("score"),
        "awayId": _team_id(away),
        "awayTeam": _team_name(away),
        "awayPoints": away.get("score"),
    }


def _boxscore_team_map(summary: dict) -> dict[str, str]:
    """ESPN team id -> school name, from the boxscore (drive/play carry only ids)."""
    out: dict[str, str] = {}
    for tb in (summary.get("boxscore") or {}).get("teams", []):
        team = tb.get("team") or {}
        name = team.get("location") or team.get("displayName")
        if team.get("id") is not None and name:
            out[str(team["id"])] = name
    return out


def reshape_drives(summary: dict, event_id: int) -> list[dict]:
    """Summary drives -> CFBD ``/drives`` dicts (offense/defense resolved by team map)."""
    names = _boxscore_team_map(summary)
    all_ids = list(names)
    out: list[dict] = []
    for i, dr in enumerate(((summary.get("drives") or {}).get("previous") or []), start=1):
        off_id = str((dr.get("team") or {}).get("id"))
        offense = names.get(off_id)
        defense = next((names[t] for t in all_ids if t != off_id), None)
        start, end = dr.get("start") or {}, dr.get("end") or {}
        out.append({
            "id": dr.get("id"),
            "gameId": event_id,
            "offense": offense,
            "defense": defense,
            "driveNumber": i,
            "scoring": dr.get("isScore"),
            "startPeriod": (start.get("period") or {}).get("number"),
            "startYardline": start.get("yardLine"),
            "startYardsToGoal": start.get("yardsToEndzone"),
            "endPeriod": (end.get("period") or {}).get("number"),
            "endYardline": end.get("yardLine"),
            "endYardsToGoal": end.get("yardsToEndzone"),
            "plays": dr.get("offensivePlays"),
            "yards": dr.get("yards"),
            "driveResult": dr.get("result") or dr.get("displayResult"),
        })
    return out


def reshape_plays(summary: dict, event_id: int) -> list[dict]:
    """Summary drives' plays -> CFBD ``/plays`` dicts (fallback path; parquet preferred)."""
    names = _boxscore_team_map(summary)
    all_ids = list(names)
    home_away = _summary_home_away(summary)
    out: list[dict] = []
    for i, dr in enumerate(((summary.get("drives") or {}).get("previous") or []), start=1):
        for p in dr.get("plays", []):
            start = p.get("start") or {}
            off_id = str((start.get("team") or {}).get("id") or (dr.get("team") or {}).get("id"))
            offense = names.get(off_id)
            defense = next((names[t] for t in all_ids if t != off_id), None)
            off_score, def_score = _play_scores(p, off_id, home_away)
            out.append({
                "id": p.get("id"),
                "gameId": event_id,
                "driveId": dr.get("id"),
                "driveNumber": i,
                "offense": offense,
                "defense": defense,
                "offenseScore": off_score,
                "defenseScore": def_score,
                "period": (p.get("period") or {}).get("number"),
                "clock": _parse_clock(p.get("clock")),
                "down": start.get("down"),
                "distance": start.get("distance"),
                "yardLine": start.get("yardLine"),
                "yardsToGoal": start.get("yardsToEndzone"),
                "yardsGained": p.get("statYardage"),
                "playType": (p.get("type") or {}).get("text"),
                "playText": p.get("text"),
                "scoring": p.get("scoringPlay"),
            })
    return out


def _summary_home_away(summary: dict) -> dict[str, str]:
    """ESPN team id -> 'home'/'away', from the boxscore."""
    out: dict[str, str] = {}
    for tb in (summary.get("boxscore") or {}).get("teams", []):
        tid = (tb.get("team") or {}).get("id")
        if tid is not None and tb.get("homeAway"):
            out[str(tid)] = tb["homeAway"]
    return out


def _play_scores(play: dict, off_id: str, home_away: dict[str, str]) -> tuple[Any, Any]:
    """Map ESPN home/away score onto CFBD offense/defense score for the play."""
    side = home_away.get(off_id)
    home, away = play.get("homeScore"), play.get("awayScore")
    if side == "home":
        return home, away
    if side == "away":
        return away, home
    return None, None


def reshape_player_game_stats(summary: dict, event_id: int) -> dict:
    """Summary ``boxscore.players`` -> one CFBD ``/games/players`` record.

    ESPN groups by stat category with a parallel ``labels`` / ``stats`` layout; CFBD
    nests team -> categories -> types -> athletes, so each label becomes a "type".
    """
    teams: list[dict] = []
    for pt in (summary.get("boxscore") or {}).get("players", []):
        team_name = (pt.get("team") or {}).get("location") or (pt.get("team") or {}).get("displayName")
        categories: list[dict] = []
        for cat in pt.get("statistics", []):
            labels = cat.get("labels") or []
            types: list[dict] = []
            for idx, label in enumerate(labels):
                athletes = [
                    {
                        "id": (a.get("athlete") or {}).get("id"),
                        "name": (a.get("athlete") or {}).get("displayName"),
                        "stat": (a.get("stats") or [])[idx] if idx < len(a.get("stats") or []) else None,
                    }
                    for a in cat.get("athletes", [])
                ]
                types.append({"name": label, "athletes": athletes})
            categories.append({"name": cat.get("name"), "types": types})
        teams.append({"team": team_name, "categories": categories})
    return {"id": event_id, "teams": teams}


def reshape_team_game_stats(summary: dict, event_id: int) -> dict:
    """Summary ``boxscore.teams`` -> one CFBD ``/games/teams`` record."""
    teams: list[dict] = []
    for tb in (summary.get("boxscore") or {}).get("teams", []):
        team_name = (tb.get("team") or {}).get("location") or (tb.get("team") or {}).get("displayName")
        stats = [
            {"category": s.get("name"), "stat": s.get("displayValue")}
            for s in tb.get("statistics", [])
        ]
        teams.append({"team": team_name, "stats": stats})
    return {"id": event_id, "teams": teams}


def reshape_rankings_week(rankings_json: dict, season_type: str) -> dict:
    """One ESPN ``/rankings`` payload -> one CFBD ranking-week record."""
    polls_out: list[dict] = []
    week = None
    for poll in rankings_json.get("rankings", []):
        week = (poll.get("occurrence") or {}).get("number")
        ranks = [
            {
                "rank": r.get("current"),
                "school": (r.get("team") or {}).get("location") or (r.get("team") or {}).get("nickname"),
                "conference": None,
                "points": r.get("points"),
                "firstPlaceVotes": r.get("firstPlaceVotes"),
            }
            for r in poll.get("ranks", [])
        ]
        polls_out.append({"poll": poll.get("name"), "ranks": ranks})
    return {"week": week, "seasonType": season_type, "polls": polls_out}


def reshape_roster(roster_json: dict) -> list[dict]:
    """ESPN team roster -> CFBD ``/roster`` dicts (athletes flattened across groups)."""
    team = roster_json.get("team") or {}
    team_name = team.get("location") or team.get("displayName")
    out: list[dict] = []
    for group in roster_json.get("athletes", []):
        for a in group.get("items", []):
            first = a.get("firstName")
            last = a.get("lastName")
            if not first and not last and a.get("fullName"):
                first, _, last = a["fullName"].partition(" ")
            birth = a.get("birthPlace") or {}
            out.append({
                "id": a.get("id"),
                "firstName": first,
                "lastName": last,
                "team": team_name,
                "position": (a.get("position") or {}).get("abbreviation"),
                "height": a.get("height"),
                "weight": a.get("weight"),
                "jersey": a.get("jersey"),
                "homeCity": birth.get("city"),
                "homeState": birth.get("state"),
            })
    return out


# --- ESPNSource -----------------------------------------------------------

class ESPNSource:
    """Key-free ESPN core source implementing the ``DataSource`` protocol.

    ``years`` (the seasons the ingest will touch) lets :meth:`venues` populate the
    venues table up front; the scoreboard fetches it makes are cached and reused by
    :meth:`games`/:meth:`weeks`, so it costs no extra requests.
    """

    def __init__(self, years: list[int] | None = None, base_url: str | None = None) -> None:
        self._years = list(years) if years else []
        self._base_url = base_url or ESPN_BASE
        self._client = None  # lazy httpx.Client
        self._events: dict[tuple[int, str], list[dict]] = {}
        self._summaries: dict[int, dict] = {}

    # -- HTTP (lazy client; retried on throttle/5xx) --
    def _get(self, path: str, **params: Any) -> Any:
        import time

        import httpx

        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                headers={"User-Agent": "gridiron-ingest/0.1", "Accept": "application/json"},
                timeout=60.0,
            )
        clean = {k: v for k, v in params.items() if v is not None}
        for attempt in range(4):
            resp = self._client.get(path, params=clean)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"ESPN request failed: {path}")

    # -- event sweep (cached per year+season_type) --
    def _season_events(self, year: int, season_type: str) -> list[dict]:
        key = (year, season_type)
        if key in self._events:
            return self._events[key]
        seasontype = _SEASONTYPE[season_type]
        events: dict[str, dict] = {}
        empty = 0
        for week in range(1, _MAX_WEEK[season_type] + 1):
            if empty >= 2:
                break
            data = self._get(
                "/scoreboard", dates=year, week=week, seasontype=seasontype, groups=80
            )
            evs = data.get("events", []) if isinstance(data, dict) else []
            if not evs:
                empty += 1
                continue
            empty = 0
            for e in evs:  # dedup by id defends against a week echoing another's games
                events[str(e.get("id"))] = e
        result = list(events.values())
        self._events[key] = result
        return result

    def _get_core(self, path: str, **params: Any) -> Any:
        """GET from the ESPN *core* API (absolute URL; overrides the client base)."""
        return self._get(ESPN_CORE + path, **params)

    def _fbs_team_ids(self, year: int) -> set[str] | None:
        """FBS team ids for ``year`` (group 80), or None if the list is unavailable.

        Returning None makes :meth:`teams` fall back to unfiltered rather than fail.
        """
        try:
            data = self._get_core(f"/seasons/{year}/types/2/groups/80/teams", limit=400)
        except Exception:
            return None
        ids = {
            item.get("$ref", "").split("/teams/")[-1].split("?")[0]
            for item in data.get("items", [])
        }
        ids.discard("")
        return ids or None

    def _summary(self, event_id: int) -> dict:
        if event_id not in self._summaries:
            self._summaries[event_id] = self._get("/summary", event=event_id)
        return self._summaries[event_id]

    def _week_events(self, year: int, week: int, season_type: str) -> list[dict]:
        return [
            e for e in self._season_events(year, season_type)
            if (e.get("week") or {}).get("number") == week
        ]

    # -- DataSource protocol --
    def teams(self, year: int) -> list[dict[str, Any]]:
        data = self._get("/teams", limit=900)
        entries = data["sports"][0]["leagues"][0]["teams"]
        teams = [reshape_team(e) for e in entries]
        fbs = self._fbs_team_ids(year)  # /teams can't filter to FBS; the core API can
        if fbs is not None:
            teams = [t for t in teams if str(t["id"]) in fbs]
        return teams

    def venues(self) -> list[dict[str, Any]]:
        seen: dict[str, dict] = {}
        for year in self._years:
            for st in (REGULAR, POSTSEASON):
                for e in self._season_events(year, st):
                    v = ((e.get("competitions") or [{}])[0]).get("venue") or {}
                    if v.get("id") is not None:
                        seen[str(v["id"])] = reshape_venue(v)
        return list(seen.values())

    def games(self, year: int, season_type: str) -> list[dict[str, Any]]:
        out = [reshape_game(e, season_type) for e in self._season_events(year, season_type)]
        return [g for g in out if g is not None]

    def drives(self, year: int, season_type: str) -> list[dict[str, Any]]:
        out: list[dict] = []
        for e in self._season_events(year, season_type):
            out.extend(reshape_drives(self._summary(int(e["id"])), int(e["id"])))
        return out

    def plays(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        out: list[dict] = []
        for e in self._week_events(year, week, season_type):
            out.extend(reshape_plays(self._summary(int(e["id"])), int(e["id"])))
        return out

    def player_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return [
            reshape_player_game_stats(self._summary(int(e["id"])), int(e["id"]))
            for e in self._week_events(year, week, season_type)
        ]

    def team_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return [
            reshape_team_game_stats(self._summary(int(e["id"])), int(e["id"]))
            for e in self._week_events(year, week, season_type)
        ]

    def rankings(self, year: int) -> list[dict[str, Any]]:
        records: dict[int | None, dict] = {}
        for st in (REGULAR, POSTSEASON):
            seasontype = _SEASONTYPE[st]
            for week in range(1, _MAX_WEEK[st] + 1):
                data = self._get("/rankings", year=year, week=week, seasontype=seasontype)
                if not data.get("rankings"):
                    continue
                rec = reshape_rankings_week(data, st)
                records[rec["week"]] = rec  # dedup by actual occurrence week
        return list(records.values())

    def rosters(self, year: int) -> list[dict[str, Any]]:
        # ESPN's roster endpoint only serves the current roster; never fabricate a
        # current roster for a past season.
        if year != current_season():
            return []
        out: list[dict] = []
        for team in self.teams(year):
            tid = team.get("id")
            if tid is None:
                continue
            data = self._get(f"/teams/{tid}/roster")
            out.extend(reshape_roster(data))
        return out

    def weeks(self, year: int, season_type: str) -> list[int]:
        nums = {
            (e.get("week") or {}).get("number")
            for e in self._season_events(year, season_type)
        }
        return sorted(n for n in nums if n is not None)

    # Categories ESPN can't serve cleanly — empty here; HybridSource routes to CFBD.
    def recruiting_teams(self, year: int) -> list[dict[str, Any]]:
        return []

    def transfers(self, year: int) -> list[dict[str, Any]]:
        return []

    def coaches(self, year: int) -> list[dict[str, Any]]:
        return []

    def betting_lines(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return []


# --- HybridSource ---------------------------------------------------------

class HybridSource:
    """ESPN for the core; CFBD only for recruiting/transfers/coaches/betting lines.

    The CFBD source is built lazily via ``extras_factory`` on first extra access, so
    an ingest that touches no extras (or disables them) never needs a CFBD key.
    """

    _CORE = (
        "teams", "venues", "games", "drives", "plays",
        "player_game_stats", "team_game_stats", "rankings", "rosters", "weeks",
    )
    _EXTRAS = ("recruiting_teams", "transfers", "coaches", "betting_lines")

    def __init__(self, core, extras=None, extras_factory=None) -> None:
        self._core = core
        self._extras = extras
        self._extras_factory = extras_factory

    def _extra(self):
        if self._extras is None:
            if self._extras_factory is None:
                raise RuntimeError("HybridSource needs an extras source or factory")
            self._extras = self._extras_factory()
        return self._extras

    def __getattr__(self, name: str):
        if name in self._CORE:
            return getattr(self._core, name)
        if name in self._EXTRAS:
            return getattr(self._extra(), name)
        raise AttributeError(name)
