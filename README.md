# 🏈 Gridiron

Statistical analysis of college football **teams, players, and games** — scores,
plays, player/team box scores, scoring types, and *where on the field* points are
scored — with data from 2014 to present (and back to ~2001 where available).

## Data sources

Gridiron ingests from [CollegeFootballData (CFBD)](https://collegefootballdata.com),
the de-facto open dataset for college football analytics.

| Source | What | How |
| --- | --- | --- |
| **CFBD API v2** (`CFBDSource`) | games, drives, play-by-play, player/team box scores, rankings | free API key, `httpx` |
| **cfbfastR bulk parquet** (`load_pbp_parquet`) | full-season play-by-play incl. EPA/WP, back to 2002 | parquet from the sportsdataverse releases; best for large backfills without hitting API limits |
| **JSON fixtures** (`FixtureSource`) | offline slice used by the tests/demo | files under `tests/fixtures/` |

> Get a free CFBD key at <https://collegefootballdata.com/key>. Personal/hobby use
> is free; revisit CFBD's Patreon tiers/terms before any public or commercial use.

## Architecture

```
gridiron/
  config.py                # settings (DATABASE_URL, CFBD_API_KEY)
  db/{models,session,init_db}.py
  ingest/{sources,transforms,pipeline,cli}.py
  analytics/queries.py     # scoring-by-field-position, FG success, PPA leaders, ...
  api/main.py              # FastAPI JSON API + Jinja pages
  web/templates/           # index + game detail
migrations/                # Alembic
tests/                     # unit + end-to-end (offline via fixtures)
```

The `plays` table is the analytical core: `play_type` + `scoring` +
`points_scored` + `yard_line`/`yards_to_goal` capture *what kind of points were
scored and from what field position* for every play.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,cfbd,postgres,bulk]"

# 1. Bring up Postgres (or skip and use the default SQLite DB)
docker compose up -d db
cp .env.example .env            # set DATABASE_URL + CFBD_API_KEY

# 2. Create the schema
alembic upgrade head            # or: python -m gridiron.db.init_db

# 3. Backfill (needs CFBD_API_KEY). One season, or a range:
python -m gridiron.ingest.cli --year 2023
python -m gridiron.ingest.cli --start 2014 --end 2024

# 4. Serve the site
uvicorn gridiron.api.main:app --reload   # http://127.0.0.1:8000
```

No API key yet? Ingest the bundled fixture and explore the app immediately:

```bash
python -m gridiron.ingest.cli --init-db --year 2023 \
    --fixtures tests/fixtures/season2023
uvicorn gridiron.api.main:app --reload
```

## Bulk historical backfill (plays with EPA/WP)

The CFBD `/plays` API is per-week and rate-limited, and only carries `ppa`. For
history, load plays from the cfbfastR **parquet** (2002–present, EPA/WP included)
while games/drives/rankings come from the API:

```bash
# Plays from parquet, the rest from CFBD (needs CFBD_API_KEY):
python -m gridiron.ingest.cli --start 2002 --end 2024 --plays-source parquet

# Fully offline — synthesize games from the parquet, no key required:
python -m gridiron.ingest.cli --start 2014 --end 2024 \
    --plays-source parquet --stub-games

# Confirm a season's parquet columns before loading (no DB writes):
python -m gridiron.ingest.cli --year 2023 --inspect-parquet
```

The load is idempotent per season (delete-by-season + bulk insert). Once plays
carry EPA/WP, the `ppa-leaders` and `ppa-by-down` views use EPA instead of `ppa`.
`--stub-games` writes minimal games (teams + final score); a later API run
enriches those same rows.

## Pages

- `/` — season game browser (filter by week & conference) · `/games/{id}` — box score & play-by-play
- `/teams` · `/teams/{team}?season=2023` — team dossier: record, home/away & by-quarter
  **splits**, scoring/efficiency charts, and a **filter bar** (week range, home/away,
  conference, vs-ranked, down, distance) that re-scopes the charts
- `/players?q=…` · `/players/{id}` — player profile, season totals, game log
- `/players/compare?a=…&b=…` — two players' stat lines side by side
- `/compare?a=Georgia&b=Alabama&season=2023` — two teams side by side + head-to-head

- `/standings?season=&conference=` — conference standings (conference + overall)
- `/leaders?season=` — win-probability, **player WPA**, **CLV**, and PPA/EPA leaderboards
- `/coaches` · `/coaches/{name}` — winningest-coaches board + a coach career page

Team pages also carry **drive-level** stats (scoring %, points/yards/plays per
drive + a drive-outcome chart), an **against-the-spread / over-under** record, a
**season-over-season** trend chart, a **recruiting-class rank + transfer-portal**
panel (in/out), the **head coach + record**, and a **closing-line-value (CLV)**
record. Player pages show a **WPA** (win probability added) total. Game pages show
the **betting lines with open→close movement** and a **win-probability** game-flow
chart.

Charts are [Plotly](https://plotly.com/python/); the JS bundle is served from the
installed `plotly` package at `/vendor/plotly.min.js`, so charts work offline with
no CDN or build step.

## Key API endpoints

- `GET /api/games?season=2023` — season schedule/results
- `GET /api/games/{id}` — game, drives, play-by-play, box score
- `GET /api/teams/{team}?season=2023` — summary, game log, rankings history
- `GET /api/players?q=…&season=2023` · `GET /api/players/{id}` — profile, season stats, game log
- `GET /api/compare?a=Georgia&b=Alabama&season=2023` — two-team comparison + head-to-head
- `GET /api/analytics/scoring-by-field-position?season=2023`
- `GET /api/analytics/scoring-types` · `/fg-success` · `/play-type-mix` · `/ppa-leaders` · `/team-scoring`
- `GET /api/analytics/success-rate` · `/explosiveness` · `/ppa-by-down` (params: `season`, optional `team`)

## Keeping the current season fresh

Ingestion is idempotent, so re-running it just upserts. `gridiron-refresh`
(re-)ingests the in-progress season (Aug–Jan → that year):

```bash
gridiron-refresh                 # current season via CFBD API
gridiron-refresh --plays-source parquet   # or pull plays from the parquet
```

Schedule it with cron — e.g. every 6 hours during the season:

```cron
0 */6 * * *  cd /path/to/gridiron && /path/to/.venv/bin/gridiron-refresh >> refresh.log 2>&1
```

## Tests

```bash
pytest          # runs fully offline against the JSON fixtures
```

## Roadmap

- **M1 (done):** ingestion pipeline + schema + analytics core + offline tests.
- **M2 (done):** roster ingestion, team & player pages, Plotly charts, and advanced
  metrics (success rate, explosiveness, PPA/EPA by down).
- **M3 (done):** bulk parquet backfill to 2002 with EPA/WP; season splits &
  composable filters; two-team matchup/comparison views; and a `gridiron-refresh`
  command (+ cron) for the current season.
- **M4 (done):** player-vs-player comparison, drive-level analytics, betting-line
  ingestion (lines + ATS/OU records), and season-over-season trends.
- **M5 (done):** win-probability charts (game flow + leaderboard), conference
  standings, and recruiting-ranking + transfer-portal ingestion.
- **M6 (done):** player win-probability-added (WPA) leaders + player-page stat, and
  coaching records (career page, team panel, browse, winningest leaderboard).
- **M7 (done):** betting-market closing-line-value (CLV) — opening vs closing line
  movement, per-game movement, team CLV records, and a CLV leaderboard.
- **Next idea:** a public read-only deploy.
