#!/usr/bin/env sh
# Container entrypoint: migrate, seed the read-only demo on first boot, then serve.
set -e

# Ensure the SQLite volume directory exists (harmless for Postgres URLs).
mkdir -p /data

# 1) Bring the schema up to date.
alembic upgrade head

# 2) Seed the bundled 2023 fixture ONLY when the DB is empty, so a real operator
#    backfill is never overwritten. Disable entirely with SEED_FIXTURES=0.
if [ "${SEED_FIXTURES:-1}" = "1" ]; then
  python - <<'PY'
from sqlalchemy import text
from gridiron.db.session import session_scope
with session_scope() as s:
    teams = s.execute(text("select count(*) from teams")).scalar() or 0
if teams:
    print(f"[entrypoint] data present ({teams} teams) — skipping demo seed")
else:
    from gridiron.ingest.pipeline import ingest_season
    from gridiron.ingest.sources import FixtureSource
    report = ingest_season(FixtureSource("tests/fixtures/season2023"), 2023)
    print(f"[entrypoint] seeded read-only demo fixtures: {report}")
PY
fi

# 3) Serve (read-only web API + pages). PORT is overridable by the platform.
exec uvicorn gridiron.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
