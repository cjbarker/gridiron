# ESPN Data Source Adapter — Design

**Date:** 2026-08-16
**Status:** Approved (pending implementation plan)
**Motivation:** Reduce dependence on the CollegeFootballData (CFBD) API — specifically its
required API key and per-key rate limits — for the high-volume core of an ingest.

## Goal

Add an ESPN-backed data source that satisfies the existing
`gridiron.ingest.sources.DataSource` protocol so it drops into the pipeline with no
changes to `pipeline.py`, `transforms.py`, or the database. ESPN's public site API needs
**no key** and is more forgiving on rate limits, so it covers the bulk data; a small,
low-volume residual stays on CFBD.

## Non-goals (YAGNI)

- Not replacing CFBD entirely — recruiting, transfers, coaches, and betting lines stay on CFBD.
- No async/concurrent fetching, no on-disk response caching, no new dependencies.
- Default source stays `cfbd`; this is purely additive and backward-compatible.

## Decisions (from brainstorming)

1. **Hybrid, not replacement.** ESPN serves the core; CFBD fills the four extras it can't
   serve cleanly (recruiting_teams, transfers, coaches, betting_lines).
2. **Parquet stays the plays path.** Backfills run `--plays-source parquet` (rate-limit-free
   bulk with EPA/WP). `ESPNSource.plays()` is implemented as a working fallback but is not the
   default path.

## Key correctness fact — ID alignment (verified)

CFBD game and team IDs **are** ESPN IDs. Verified against `tests/fixtures/season2023/games.json`:
game `id=401520281` (an ESPN event id), `homeId=61` (ESPN's Georgia), `awayId=333` (ESPN's
Alabama). Consequences:

- ESPN games/teams land on the **same primary keys** the DB already uses.
- The cfbfastR parquet's `game_id` (also an ESPN event id) joins ESPN-ingested games cleanly.
- CFBD extras are keyed by team **name + season**, so they compose with ESPN core regardless.
- ESPN **drive** ids may not match the parquet's `drive_id`; this is already handled —
  `_ingest_plays_parquet` nulls any `drive_id` not present in `known_drives`. Graceful, not fatal.

`Game.venue_id` is a nullable FK to `venues` and SQLite runs without `PRAGMA foreign_keys=ON`,
so an unpopulated venues table would not error — but ESPN embeds venue objects in the scoreboard,
so venues are populated anyway for data quality.

## Architecture

Three pieces, all implementing `DataSource`:

### 1. `ESPNSource` — new module `gridiron/ingest/espn.py`

Talks to ESPN's public site API and **reshapes ESPN JSON into CFBD-field-named dicts** so the
existing `transforms.py` accessors work unchanged. Base URL:
`https://site.api.espn.com/apis/site/v2/sports/football/college-football`.
Lazy `httpx.Client` (mirrors `CFBDSource`), a `User-Agent` header, and small retry/backoff on
429/5xx.

Internal fetch helpers with per-instance caches:

- **`_season_events(year, season_type)`** — fetches each week's
  `scoreboard?dates=Y&week=W&seasontype=T&groups=80` (groups=80 = FBS) and caches the event
  lists. Backs `games()`, `venues()`, and `weeks()` from one sweep.
- **`_summary(event_id)`** — fetches `/summary?event=ID` once per game, cached. Backs `drives()`,
  `player_game_stats()`, `team_game_stats()`, and the `plays()` fallback.
- Caches are **cleared when `year` changes** so a multi-year backfill does not accumulate every
  season's summaries in memory. (The CLI reuses one source instance across years.)

`season_type` mapping: CFBD `regular`→ESPN `seasontype=2`, `postseason`→`seasontype=3`.

### 2. `HybridSource` — same module

Composes the two: core methods delegate to an `ESPNSource`; the four extras delegate to a
lazily-constructed `CFBDSource`. The CFBD client is only built when an extra is actually called,
so `--source hybrid` with all extras disabled (`--no-recruiting --no-coaches --no-lines` and no
recruiting/transfers) needs **no key**.

### 3. CLI wiring — `gridiron/ingest/cli.py`

- New flag: `--source {cfbd,espn,hybrid}`, default `cfbd` (backward-compatible).
- `_build_source` selects the class. `--fixtures` still overrides to `FixtureSource`.
- The offline parquet/stub-games path is unaffected.

## Method-by-method mapping (ESPN → CFBD shape)

| Protocol method | ESPN endpoint / source | Reshape notes |
|---|---|---|
| `teams(year)` | `/teams?groups=80` (FBS) | `id, abbreviation, color, alternateColor, logos`; `location`→school, `nickname`→mascot |
| `venues()` | derived from cached scoreboards | dedup by venue `id`; `fullName`→name, `address.city/state`, `indoor`→dome |
| `games(year, st)` | cached scoreboards | event `id`→id; competitors by `homeAway` → home/away `id`,`score`,`team`; `neutralSite`, `conferenceCompetition`→conference_game, `attendance`, venue `id` |
| `drives(year, st)` | `_summary(event).drives.previous[]` | inject `gameId=event_id`; drive `id`, `team`→offense, `result`/`displayResult`→drive_result, `yards`, `offensivePlays`→plays, `isScore`→scoring, start/end period+yardline |
| `plays(year, wk, st)` | summary drives' `plays[]` (fallback) | inject `gameId`, `driveId`; `type.text`→play_type, `text`, `period.number`, `clock`, `scoringPlay`, `start/end` yardage, scores |
| `player_game_stats(year, wk, st)` | `_summary(event).boxscore.players[]` | per team, per stat category (`passing`/`rushing`/…) with `labels` + athlete rows → CFBD `/games/players` nested shape |
| `team_game_stats(year, wk, st)` | `_summary(event).boxscore.teams[]` | `statistics[]` (`name`,`displayValue`) → CFBD `/games/teams` nested shape |
| `rankings(year)` | `/rankings` per week | polls→`{week, seasonType, polls:[{poll:name, ranks:[{rank:current, school, conference, points, firstPlaceVotes}]}]}` |
| `rosters(year)` | `/teams/{id}/roster?season=Y` per FBS team | flatten `athletes[].items[]`; `id, fullName, jersey, position, weight, height`, carry `team` |
| `weeks(year, st)` | derived from cached scoreboards / season calendar | distinct week numbers that have events |
| `recruiting_teams`, `transfers`, `coaches`, `betting_lines` | **CFBD** (via HybridSource) | ESPNSource returns `[]` for these |

## Error handling

- Retry with backoff on HTTP 429 / 5xx; raise on persistent failure (same posture as `CFBDSource`).
- Games not yet played (empty/partial summary) yield nothing rather than raising.
- `weeks()`/`games()` for a not-yet-started season return `[]` cleanly.

## Testing (TDD, fully offline)

- Capture trimmed real ESPN JSON as fixtures under `tests/fixtures/espn/` (scoreboard, summary,
  rankings, teams, roster).
- **Reshape unit tests:** ESPN fixture → `ESPNSource` reshape → assert `transforms.to_*` produce
  correct ORM rows. Explicitly cover: game/team **ID alignment**, and the rankings shape
  (regression guard for the duplicate-poll `uq_ranking` crash).
- **End-to-end offline test:** an `ESPNFixtureSource` (or monkeypatched `_get`) drives a full
  `ingest_season`, mirroring the existing `FixtureSource` suite. No network in CI.
- **Hybrid test:** confirm extras route to CFBD and core routes to ESPN; confirm no CFBD client is
  constructed when extras are disabled.

## Rollout / usage

```bash
# Key-free core + bulk plays, CFBD only for the low-volume extras:
uv run gridiron-ingest --source hybrid --start 2014 --end 2024 --plays-source parquet

# Fully key-free (drops recruiting/transfers/coaches/lines):
uv run gridiron-ingest --source espn --year 2023 --no-recruiting --no-coaches --no-lines
```

## Open questions / follow-ups (out of scope for this spec)

- ESPN teams-endpoint FBS filtering (`groups=80` vs core-API classification) — settle during impl.
- Season week-count discovery (calendar vs. probing) — settle during impl with a fixture.
