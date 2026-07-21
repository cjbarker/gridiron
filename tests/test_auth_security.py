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


# --- U3: security core ------------------------------------------------------

def test_password_hash_roundtrip():
    from gridiron.auth.security import hash_password, verify_password

    hashed = hash_password("s3cret-pw")
    assert hashed != "s3cret-pw"
    assert hashed.startswith("$argon2")
    assert verify_password("s3cret-pw", hashed) is True
    assert verify_password("wrong", hashed) is False
    # OAuth-only users (no stored hash) never verify.
    assert verify_password("anything", None) is False


def test_hash_token_deterministic():
    from gridiron.auth.security import hash_token

    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert len(hash_token("abc")) == 64


def test_load_user_active_and_inactive(db_env):
    from gridiron.auth.security import load_user
    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        s.add(User(email="active@x.com", is_active=True))
        s.add(User(email="inactive@x.com", is_active=False))

    with session_scope() as s:
        active = s.query(User).filter_by(email="active@x.com").one()
        inactive = s.query(User).filter_by(email="inactive@x.com").one()
        assert load_user(s, active.id) is not None
        assert load_user(s, inactive.id) is None
        assert load_user(s, None) is None
        assert load_user(s, 999999) is None


def test_require_admin_enforces_role():
    from fastapi import HTTPException

    from gridiron.auth.security import require_admin
    from gridiron.db.models import User

    admin = User(email="a@x.com", role="admin")
    assert require_admin(user=admin) is admin
    with pytest.raises(HTTPException) as exc:
        require_admin(user=User(email="u@x.com", role="user"))
    assert exc.value.status_code == 403


def test_csrf_issue_and_verify():
    from types import SimpleNamespace

    from gridiron.auth.security import issue_csrf, verify_csrf

    req = SimpleNamespace(session={})
    token = issue_csrf(req)
    assert token and req.session["csrf_token"] == token
    assert issue_csrf(req) == token  # stable within a session
    assert verify_csrf(req, token) is True
    assert verify_csrf(req, "tampered") is False
    assert verify_csrf(req, None) is False
    assert verify_csrf(SimpleNamespace(session={}), token) is False  # no session token
