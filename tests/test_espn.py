"""ESPN data source adapter.

The adapter's job is to reshape ESPN's JSON into the CFBD-field-named dicts that
``transforms.py`` already knows how to normalize. So every reshape test asserts on
the *ORM row* the existing transform produces from the reshaped dict — that is the
real contract. Fixtures under ``tests/fixtures/espn/`` are trimmed real responses.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gridiron.ingest import espn
from gridiron.ingest import transforms as tf

FIX = Path(__file__).parent / "fixtures" / "espn"
EVID = 401523986  # SJSU @ USC, 2023 wk1 (the summary/scoreboard fixture)


def load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def _team_entry(team_id: str) -> dict:
    for e in load("teams")["sports"][0]["leagues"][0]["teams"]:
        if e["team"]["id"] == team_id:
            return e
    raise KeyError(team_id)


# --- Pure reshapers: ESPN dict -> CFBD dict -> ORM row --------------------

def test_reshape_team():
    t = tf.to_team(espn.reshape_team(_team_entry("333")))
    assert t.id == 333
    assert t.school == "Alabama"
    assert t.mascot == "Crimson Tide"
    assert t.abbreviation == "ALA"
    assert t.color == "9e1b32"
    assert t.alt_color == "ffffff"
    assert isinstance(t.logo, str) and t.logo.endswith("333.png")


def test_reshape_game():
    g = tf.to_game(espn.reshape_game(load("scoreboard")["events"][0], "regular"))
    assert g.id == EVID
    assert g.season == 2023 and g.week == 1
    assert g.season_type == "regular"
    assert g.completed is True
    assert g.home_id == 30 and g.home_team == "USC" and g.home_points == 56
    assert g.away_id == 23 and g.away_team == "San José State"
    assert g.venue_id == 477
    assert g.neutral_site is False
    assert g.attendance == 63411


def test_reshape_drives():
    d0 = tf.to_drive(espn.reshape_drives(load("summary"), EVID)[0])
    assert d0.game_id == EVID
    assert d0.offense == "San José State"  # from boxscore team map (drive.team.location is null)
    assert d0.defense == "USC"
    assert d0.scoring is False
    assert d0.plays == 8
    assert d0.yards == 28
    assert d0.start_period == 1
    assert d0.start_yardline == 25
    assert d0.drive_result == "PUNT"


def test_reshape_plays():
    s = load("summary")
    plays = espn.reshape_plays(s, EVID)
    p0 = tf.to_play(plays[0], season=2023)
    assert p0.game_id == EVID
    assert p0.play_type == "Kickoff"
    assert p0.period == 1
    assert p0.clock_minutes == 15 and p0.clock_seconds == 0
    assert p0.drive_id == int(s["drives"]["previous"][0]["id"])


def test_reshape_player_game_stats():
    rec = espn.reshape_player_game_stats(load("summary"), EVID)
    rows = tf.flatten_player_game_stats(EVID, 2023, rec)
    r = next(x for x in rows if x.player == "Chevan Cordeiro" and x.stat_type == "C/ATT")
    assert r.category == "passing"
    assert r.stat == "21/38"
    assert r.team == "San José State"
    assert r.player_id == 4373934


def test_reshape_team_game_stats():
    rec = espn.reshape_team_game_stats(load("summary"), EVID)
    rows = tf.flatten_team_game_stats(EVID, 2023, rec)
    r = next(x for x in rows if x.stat_type == "firstDowns" and x.team == "San José State")
    assert r.stat == "23"


def test_reshape_rankings_week():
    rec = espn.reshape_rankings_week(load("rankings"), "regular")
    rows = tf.flatten_rankings(2023, [rec])
    r = next(x for x in rows if x.poll == "AFCA Coaches Poll" and x.team == "Ohio State")
    assert r.rank == 1
    assert r.week == 1  # occurrence.number
    assert r.season_type == "regular"
    assert r.first_place_votes == 38


def test_reshape_roster():
    p = tf.to_player(espn.reshape_roster(load("roster"))[0])
    assert p.first_name == "Ethan" and p.last_name == "Barbour"
    assert p.team == "Georgia"
    assert p.position == "TE"
    assert p.jersey == 9
    assert p.weight == 235 and p.height == 75
    assert p.home_city == "Alpharetta" and p.home_state == "GA"


# --- ESPNSource: fetch + cache wiring (offline via a fake _get) -----------

class FakeESPN(espn.ESPNSource):
    """ESPNSource with the HTTP layer replaced by fixture lookups."""

    def __init__(self, years=None):
        super().__init__(years=years)
        self.summary_calls = 0

    def _get_core(self, path: str, **params):
        return load("fbs_teams")  # FBS id list contains only 61 (Georgia)

    def _get(self, path: str, **params):
        if path == "/teams":
            return load("teams")
        if path == "/scoreboard":
            # Only week 1 has events; other weeks are empty so the sweep terminates.
            return load("scoreboard") if str(params.get("week")) == "1" else {"events": []}
        if path == "/summary":
            self.summary_calls += 1
            return load("summary")
        if path == "/rankings":
            return load("rankings") if str(params.get("week")) == "1" else {"rankings": []}
        if path.endswith("/roster"):
            return load("roster")
        raise AssertionError(f"unexpected path {path}")


def test_source_teams_filtered_to_fbs():
    # teams fixture has 61 and 333, but the FBS id list contains only 61.
    ids = {tf.to_team(t).id for t in FakeESPN(years=[2023]).teams(2023)}
    assert ids == {61}  # 333 filtered out as non-FBS


def test_source_games():
    games = FakeESPN(years=[2023]).games(2023, "regular")
    assert len(games) == 1 and tf.to_game(games[0]).id == EVID


def test_source_venues_from_events():
    v = tf.to_venue(FakeESPN(years=[2023]).venues()[0])
    assert v.id == 477
    assert "Coliseum" in v.name
    assert v.city == "Los Angeles"
    assert v.dome is False


def test_source_weeks():
    assert FakeESPN(years=[2023]).weeks(2023, "regular") == [1]


def test_source_summary_is_cached_across_categories():
    src = FakeESPN(years=[2023])
    src.drives(2023, "regular")
    src.player_game_stats(2023, 1, "regular")
    src.team_game_stats(2023, 1, "regular")
    assert src.summary_calls == 1  # one /summary fetch reused across all three


def test_source_rosters_current_only(monkeypatch):
    monkeypatch.setattr(espn, "current_season", lambda: 2023)
    src = FakeESPN(years=[2023])
    assert len(src.rosters(2023)) >= 1
    assert src.rosters(2013) == []  # historical rosters skipped, never fabricated


# --- HybridSource: core -> ESPN, extras -> CFBD --------------------------

class FakeExtras:
    def __init__(self):
        self.hits = []

    def recruiting_teams(self, year):
        self.hits.append("recruiting")
        return [{"team": "Georgia"}]

    def transfers(self, year):
        return []

    def coaches(self, year):
        return []

    def betting_lines(self, year, season_type):
        return []


def test_hybrid_routes_core_to_espn_extras_to_cfbd():
    h = espn.HybridSource(core=FakeESPN(years=[2023]), extras=FakeExtras())
    assert tf.to_game(h.games(2023, "regular")[0]).id == EVID
    assert h.recruiting_teams(2023) == [{"team": "Georgia"}]


def test_hybrid_builds_cfbd_lazily():
    built = []

    def factory():
        built.append(1)
        return FakeExtras()

    h = espn.HybridSource(core=FakeESPN(years=[2023]), extras_factory=factory)
    h.games(2023, "regular")
    assert built == []  # no CFBD constructed for core-only work
    h.coaches(2023)
    assert built == [1]  # constructed on first extra access


# --- CLI wiring ----------------------------------------------------------

def _args(**over):
    base = dict(
        fixtures=None, source="cfbd", api_key=None,
        plays_source="api", stub_games=False, parquet_base_url=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_build_source_selects_espn_and_hybrid():
    from gridiron.ingest.cli import _build_source

    assert isinstance(_build_source(_args(source="espn")), espn.ESPNSource)
    assert isinstance(_build_source(_args(source="hybrid")), espn.HybridSource)
