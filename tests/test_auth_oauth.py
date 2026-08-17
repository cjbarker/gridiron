"""Google SSO tests (U6). The Google token exchange is mocked — no network."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db_env):
    from gridiron.api.main import app

    return TestClient(app)


class _FakeGoogle:
    def __init__(self, userinfo=None, raises=False):
        self._userinfo = userinfo
        self._raises = raises

    async def authorize_access_token(self, request):
        if self._raises:
            raise ValueError("state mismatch")
        return {"userinfo": self._userinfo}


class _FakeOAuth:
    def __init__(self, google):
        self.google = google


def _patch_oauth(monkeypatch, *, userinfo=None, raises=False):
    from gridiron.auth import oauth as oauth_mod

    monkeypatch.setattr(
        oauth_mod, "get_oauth", lambda: _FakeOAuth(_FakeGoogle(userinfo, raises))
    )


def _counts():
    from sqlalchemy import func, select

    from gridiron.db.models import OAuthAccount, User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        users = s.scalar(select(func.count()).select_from(User))
        accounts = s.scalar(select(func.count()).select_from(OAuthAccount))
        return users, accounts


def test_callback_creates_new_user(client, monkeypatch):
    _patch_oauth(
        monkeypatch, userinfo={"sub": "g-1", "email": "New@Gmail.com", "email_verified": True}
    )
    resp = client.get("/auth/google/callback?state=s&code=c", follow_redirects=False)
    assert resp.status_code == 303
    assert _counts() == (1, 1)
    # Session established — verified email stored lowercased.
    assert "new@gmail.com" in client.get("/").text


def test_callback_returning_identity_reuses_user(client, monkeypatch):
    info = {"sub": "g-2", "email": "repeat@gmail.com", "email_verified": True}
    _patch_oauth(monkeypatch, userinfo=info)
    client.get("/auth/google/callback?state=s&code=c", follow_redirects=False)
    # Second sign-in with the same Google sub.
    other = TestClient(client.app)
    _patch_oauth(monkeypatch, userinfo=info)
    other.get("/auth/google/callback?state=s&code=c", follow_redirects=False)
    assert _counts() == (1, 1)  # no duplicate user or account


def test_callback_links_to_existing_password_user_when_verified(client, monkeypatch):
    from gridiron.auth.security import hash_password
    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        s.add(User(email="shared@gmail.com", hashed_password=hash_password("password123")))

    _patch_oauth(
        monkeypatch, userinfo={"sub": "g-3", "email": "Shared@Gmail.com", "email_verified": True}
    )
    resp = client.get("/auth/google/callback?state=s&code=c", follow_redirects=False)
    assert resp.status_code == 303
    # Same user, now with a linked Google account — no duplicate user row.
    assert _counts() == (1, 1)


def test_callback_unverified_email_does_not_link(client, monkeypatch):
    # Security: a Google token with an UNVERIFIED victim email must NOT link to
    # or authenticate as that victim's existing password account.
    from gridiron.auth.security import hash_password
    from sqlalchemy import select

    from gridiron.db.models import OAuthAccount, User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        s.add(User(email="victim@gmail.com", hashed_password=hash_password("password123")))

    _patch_oauth(
        monkeypatch, userinfo={"sub": "attacker", "email": "victim@gmail.com", "email_verified": False}
    )
    resp = client.get("/auth/google/callback?state=s&code=c", follow_redirects=False)
    assert resp.status_code == 303
    # A separate, sub-keyed placeholder account was created — the victim row is untouched.
    assert _counts() == (2, 1)
    with session_scope() as s:
        linked_user_id = s.scalar(
            select(OAuthAccount.user_id).where(OAuthAccount.provider_account_id == "attacker")
        )
        victim_id = s.scalar(select(User.id).where(User.email == "victim@gmail.com"))
        assert linked_user_id != victim_id


def test_callback_missing_sub_rejected(client, monkeypatch):
    _patch_oauth(monkeypatch, userinfo={"email": "x@gmail.com", "email_verified": True})
    resp = client.get("/auth/google/callback?state=s&code=c", follow_redirects=False)
    assert resp.status_code == 400
    assert _counts() == (0, 0)


def test_callback_invalid_state_rejected(client, monkeypatch):
    _patch_oauth(monkeypatch, raises=True)
    resp = client.get("/auth/google/callback?state=bad", follow_redirects=False)
    assert resp.status_code == 400
    assert _counts() == (0, 0)


def test_google_login_unconfigured_is_404_and_button_hidden(client):
    # No GOOGLE_CLIENT_ID/SECRET in the test env -> flow unavailable.
    assert client.get("/auth/google/login", follow_redirects=False).status_code == 404
    assert "Continue with Google" not in client.get("/login").text


def test_find_or_create_missing_email(db_env):
    from gridiron.auth.oauth import find_or_create_google_user
    from gridiron.db.session import session_scope

    with session_scope() as s:
        user = find_or_create_google_user(s, "g-noemail", None)
        assert user.email == "google_g-noemail@users.noreply"
        # No verified email asserted by Google -> account is not marked verified.
        assert user.is_verified is False
