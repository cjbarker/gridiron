"""Application settings, loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Values are read from environment variables (or a local ``.env`` file).
    ``DATABASE_URL`` defaults to a local SQLite file so the project runs with
    zero external services; point it at Postgres for real use, e.g.
    ``postgresql+psycopg://gridiron:gridiron@localhost:5432/gridiron``.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///gridiron.db"

    # CollegeFootballData API v2. Get a free key at https://collegefootballdata.com/key
    cfbd_api_key: str | None = None
    cfbd_base_url: str = "https://apinext.collegefootballdata.com"

    # Base URL for the sportsdataverse bulk play-by-play parquet releases.
    # Used by the optional bulk backfill path (see ingest/sources.py).
    pbp_parquet_base_url: str = (
        "https://github.com/sportsdataverse/sportsdataverse-data/"
        "releases/download/cfbfastR_cfb_pbp"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
