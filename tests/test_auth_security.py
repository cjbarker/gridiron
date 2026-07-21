"""Unit tests for the auth foundation: settings, models, and security core.

Covers U1 (config), U2 (data model + migration), and U3 (hashing, sessions,
current-user/role dependencies, CSRF).
"""

from __future__ import annotations


# --- U1: configuration ------------------------------------------------------

def test_settings_auth_defaults():
    from gridiron.config import Settings

    s = Settings()
    assert s.secret_key is None
    assert s.session_cookie_secure is False
    assert s.password_reset_ttl_minutes == 30
    assert s.admin_email_set() == set()


def test_admin_emails_parsing():
    from gridiron.config import Settings

    s = Settings(admin_emails=" A@X.com, b@Y.com ,")
    assert s.admin_email_set() == {"a@x.com", "b@y.com"}


def test_session_secret_configured_vs_ephemeral():
    from gridiron.config import Settings

    assert Settings(secret_key="fixed").session_secret() == "fixed"
    # Ephemeral key is stable within a process and non-empty.
    s = Settings()
    assert s.session_secret() and s.session_secret() == s.session_secret()
