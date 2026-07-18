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

## Key API endpoints

- `GET /api/games?season=2023` — season schedule/results
- `GET /api/games/{id}` — game, drives, play-by-play, box score
- `GET /api/analytics/scoring-by-field-position?season=2023`
- `GET /api/analytics/scoring-types` · `/fg-success` · `/play-type-mix` · `/ppa-leaders` · `/team-scoring`

## Tests

```bash
pytest          # runs fully offline against the JSON fixtures
```

## Roadmap

- **M1 (done):** ingestion pipeline + schema + analytics core + offline tests.
- **M2:** richer team/player pages, season splits, charts (Plotly).
- **M3:** advanced analytics (success rate, explosiveness, EPA by down/distance),
  bulk parquet backfill to 2002, current-season auto-refresh.
