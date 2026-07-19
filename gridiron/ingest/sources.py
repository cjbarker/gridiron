"""Data sources for ingestion.

A *source* returns raw records as plain ``dict``s. Transforms (``transforms.py``)
then normalize those dicts into ORM rows. This boundary lets us swap the live
CollegeFootballData (CFBD) API for on-disk JSON fixtures without touching the
rest of the pipeline — which is exactly how the test suite verifies ingestion
end-to-end without network access.

Two sources ship here:

* :class:`CFBDSource` — live CFBD API v2 (needs a free API key).
* :class:`FixtureSource` — reads JSON lists from a directory (tests / demos).

Both satisfy the same :class:`DataSource` protocol. There is also a standalone
:func:`load_pbp_parquet` helper for the optional bulk play-by-play backfill from
the sportsdataverse parquet releases.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

REGULAR = "regular"
POSTSEASON = "postseason"


@runtime_checkable
class DataSource(Protocol):
    """Everything the pipeline needs to ingest one season."""

    def teams(self, year: int) -> list[dict[str, Any]]: ...
    def venues(self) -> list[dict[str, Any]]: ...
    def games(self, year: int, season_type: str) -> list[dict[str, Any]]: ...
    def drives(self, year: int, season_type: str) -> list[dict[str, Any]]: ...
    def plays(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]: ...
    def player_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]: ...
    def team_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]: ...
    def rankings(self, year: int) -> list[dict[str, Any]]: ...
    def rosters(self, year: int) -> list[dict[str, Any]]: ...
    def betting_lines(self, year: int, season_type: str) -> list[dict[str, Any]]: ...
    def recruiting_teams(self, year: int) -> list[dict[str, Any]]: ...
    def transfers(self, year: int) -> list[dict[str, Any]]: ...
    def coaches(self, year: int) -> list[dict[str, Any]]: ...
    def weeks(self, year: int, season_type: str) -> list[int]: ...


class CFBDSource:
    """Live CollegeFootballData API v2 source.

    Requires an API key (free at https://collegefootballdata.com/key), passed
    explicitly or read from ``Settings.cfbd_api_key``.
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        import httpx  # imported lazily so the package installs without a key/network

        from gridiron.config import get_settings

        settings = get_settings()
        key = api_key or settings.cfbd_api_key
        if not key:
            raise RuntimeError(
                "No CFBD API key. Set CFBD_API_KEY in your environment/.env "
                "(get a free key at https://collegefootballdata.com/key)."
            )
        self._client = httpx.Client(
            base_url=base_url or settings.cfbd_base_url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=60.0,
        )

    def _get(self, path: str, **params: Any) -> list[dict[str, Any]]:
        clean = {k: v for k, v in params.items() if v is not None}
        resp = self._client.get(path, params=clean)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]

    def teams(self, year: int) -> list[dict[str, Any]]:
        return self._get("/teams/fbs", year=year)

    def venues(self) -> list[dict[str, Any]]:
        return self._get("/venues")

    def games(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return self._get("/games", year=year, seasonType=season_type)

    def drives(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return self._get("/drives", year=year, seasonType=season_type)

    def plays(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return self._get("/plays", year=year, week=week, seasonType=season_type)

    def player_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return self._get("/games/players", year=year, week=week, seasonType=season_type)

    def team_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return self._get("/games/teams", year=year, week=week, seasonType=season_type)

    def rankings(self, year: int) -> list[dict[str, Any]]:
        return self._get("/rankings", year=year)

    def rosters(self, year: int) -> list[dict[str, Any]]:
        """Full-season rosters. CFBD /roster is per-team, so iterate the season's
        FBS teams and concatenate (each athlete record already carries ``team``)."""
        out: list[dict[str, Any]] = []
        for team in self.teams(year):
            school = team.get("school") or team.get("team")
            if not school:
                continue
            out.extend(self._get("/roster", year=year, team=school))
        return out

    def betting_lines(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return self._get("/lines", year=year, seasonType=season_type)

    def recruiting_teams(self, year: int) -> list[dict[str, Any]]:
        return self._get("/recruiting/teams", year=year)

    def transfers(self, year: int) -> list[dict[str, Any]]:
        return self._get("/player/portal", year=year)

    def coaches(self, year: int) -> list[dict[str, Any]]:
        return self._get("/coaches", year=year)

    def weeks(self, year: int, season_type: str) -> list[int]:
        """Distinct weeks that actually have games (avoids blind 1..20 loops)."""
        games = self.games(year, season_type)
        found = {g.get("week") for g in games if g.get("week") is not None}
        return sorted(found)  # type: ignore[type-var]


class FixtureSource:
    """Reads JSON list files from a directory. Used by tests and the demo.

    Expected files (missing files are treated as empty): ``teams.json``,
    ``venues.json``, ``games.json``, ``drives.json``, ``plays.json``,
    ``player_game_stats.json``, ``team_game_stats.json``, ``rankings.json``.
    Records are returned unfiltered; the fixture is assumed to hold one slice.
    """

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)

    def _load(self, name: str) -> list[dict[str, Any]]:
        path = self.dir / f"{name}.json"
        if not path.exists():
            return []
        with path.open() as fh:
            return json.load(fh)

    def teams(self, year: int) -> list[dict[str, Any]]:
        return self._load("teams")

    def venues(self) -> list[dict[str, Any]]:
        return self._load("venues")

    def games(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return [g for g in self._load("games") if _season_type_of(g) == season_type]

    def drives(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return self._load("drives")

    def plays(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return self._load("plays")

    def player_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return self._load("player_game_stats")

    def team_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return self._load("team_game_stats")

    def rankings(self, year: int) -> list[dict[str, Any]]:
        return self._load("rankings")

    def rosters(self, year: int) -> list[dict[str, Any]]:
        return self._load("roster")

    def betting_lines(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return self._load("lines")

    def recruiting_teams(self, year: int) -> list[dict[str, Any]]:
        return self._load("recruiting_teams")

    def transfers(self, year: int) -> list[dict[str, Any]]:
        return self._load("transfers")

    def coaches(self, year: int) -> list[dict[str, Any]]:
        return self._load("coaches")

    def weeks(self, year: int, season_type: str) -> list[int]:
        # Return one nominal week; the fixture source ignores week filtering.
        return [1] if self.games(year, season_type) else []


class EmptySource:
    """A source that returns nothing — for offline parquet backfills (no CFBD key).

    Paired with ``plays_source="parquet"`` and ``stub_games=True``, the pipeline
    synthesizes games from the parquet, so no API access is needed at all.
    """

    def teams(self, year: int) -> list[dict[str, Any]]:
        return []

    def venues(self) -> list[dict[str, Any]]:
        return []

    def games(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return []

    def drives(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return []

    def plays(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return []

    def player_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return []

    def team_game_stats(self, year: int, week: int, season_type: str) -> list[dict[str, Any]]:
        return []

    def rankings(self, year: int) -> list[dict[str, Any]]:
        return []

    def rosters(self, year: int) -> list[dict[str, Any]]:
        return []

    def betting_lines(self, year: int, season_type: str) -> list[dict[str, Any]]:
        return []

    def recruiting_teams(self, year: int) -> list[dict[str, Any]]:
        return []

    def transfers(self, year: int) -> list[dict[str, Any]]:
        return []

    def coaches(self, year: int) -> list[dict[str, Any]]:
        return []

    def weeks(self, year: int, season_type: str) -> list[int]:
        return []


def _season_type_of(game: dict[str, Any]) -> str:
    return game.get("seasonType") or game.get("season_type") or REGULAR


def load_pbp_parquet(year: int, base_url: str | None = None) -> list[dict[str, Any]]:
    """Optional bulk path: download a season of cfbfastR play-by-play parquet.

    Returns rows as dicts using the cfbfastR column names (which include
    pre-computed ``EPA``/``wp`` fields). Requires ``pyarrow`` and network access
    to the sportsdataverse releases host. Prefer this for large historical
    backfills to avoid CFBD API rate limits.
    """
    import io
    import urllib.request

    import pyarrow.parquet as pq

    from gridiron.config import get_settings

    base = base_url or get_settings().pbp_parquet_base_url
    url = f"{base}/play_by_play_{year}.parquet"
    req = urllib.request.Request(url, headers={"User-Agent": "gridiron/0.1"})
    with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 (trusted host)
        raw = resp.read()
    table = pq.read_table(io.BytesIO(raw))
    return table.to_pylist()
