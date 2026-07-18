"""Test fixtures: an isolated SQLite database seeded from the JSON fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "season2023"


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    """Point the app at a throwaway SQLite DB and reset cached engine/settings."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    # Reset the cached settings and engine so they pick up the new DATABASE_URL.
    from gridiron import config
    from gridiron.db import session as db_session

    config.get_settings.cache_clear()
    db_session._engine = None
    db_session._SessionFactory = None

    from gridiron.db.init_db import create_all

    create_all()
    yield
    db_session._engine = None
    db_session._SessionFactory = None
    config.get_settings.cache_clear()


@pytest.fixture()
def fixture_source():
    from gridiron.ingest.sources import FixtureSource

    return FixtureSource(FIXTURES)
