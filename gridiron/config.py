"""Application settings, loaded from environment / .env file."""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Ephemeral session key generated once per process when SECRET_KEY is unset.
_EPHEMERAL_SECRET: str | None = None


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

    # --- Authentication -----------------------------------------------------
    # Session-cookie signing key. MUST be set in production; if unset, an
    # ephemeral per-process key is generated (dev only — sessions reset on
    # every restart). See ``session_secret``.
    secret_key: str | None = None
    # Set True behind HTTPS so the session cookie carries the Secure flag.
    session_cookie_secure: bool = False
    # Public base URL, used to build absolute links in emails (password reset).
    base_url: str = "http://127.0.0.1:8000"

    # Google OAuth (SSO). The "Continue with Google" flow is enabled only when
    # both of these are set; otherwise the button is hidden and the app runs
    # with email/password auth alone.
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # SMTP for transactional email (password reset). When unset, reset links
    # are logged to stdout via the console transport (dev default).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "gridiron@localhost"
    smtp_starttls: bool = True

    # Password-reset link lifetime.
    password_reset_ttl_minutes: int = 30

    # Comma-separated emails auto-promoted to admin on login (convenience;
    # ``gridiron-admin promote <email>`` is the primary path).
    admin_emails: str = ""

    def session_secret(self) -> str:
        """The effective session-signing key.

        Returns ``secret_key`` when configured. Otherwise generates a random
        ephemeral key (once per process) and warns — fine for local dev, but
        sessions will not survive a restart and this must not be relied on in
        production.
        """
        global _EPHEMERAL_SECRET
        if self.secret_key:
            return self.secret_key
        if _EPHEMERAL_SECRET is None:
            _EPHEMERAL_SECRET = secrets.token_urlsafe(32)
            logger.warning(
                "SECRET_KEY is not set — using an ephemeral session key. "
                "Sessions will reset on restart; set SECRET_KEY in production."
            )
        return _EPHEMERAL_SECRET

    def admin_email_set(self) -> set[str]:
        """Normalized (lowercased, trimmed) set of auto-admin emails."""
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
