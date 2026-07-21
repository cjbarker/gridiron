"""Unit tests for the auth foundation: settings, models, and security core.

Covers U1 (config), U2 (data model + migration), and U3 (hashing, sessions,
current-user/role dependencies, CSRF).
"""

from __future__ import annotations

import pytest


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


# --- U2: data model ---------------------------------------------------------

def test_user_is_admin_property():
    from gridiron.db.models import User

    assert User(email="a@x.com", role="admin").is_admin is True
    assert User(email="b@x.com", role="user").is_admin is False


def test_user_email_unique(db_env):
    from sqlalchemy.exc import IntegrityError

    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        s.add(User(email="dup@x.com", hashed_password="h"))
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            s.add(User(email="dup@x.com", hashed_password="h2"))


def test_oauth_account_unique(db_env):
    from sqlalchemy.exc import IntegrityError

    from gridiron.db.models import OAuthAccount, User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        u = User(email="o@x.com")
        s.add(u)
        s.flush()
        s.add(OAuthAccount(user_id=u.id, provider="google", provider_account_id="g1"))
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            u = s.query(User).filter_by(email="o@x.com").one()
            s.add(OAuthAccount(user_id=u.id, provider="google", provider_account_id="g1"))
