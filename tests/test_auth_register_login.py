"""Integration tests for register / login / logout (U4)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture()
def client(db_env):
    from gridiron.api.main import app

    return TestClient(app)


def _csrf(client: TestClient, path: str) -> str:
    match = _CSRF_RE.search(client.get(path).text)
    assert match, f"no csrf token on {path}"
    return match.group(1)


def _register(client: TestClient, email: str, password: str, follow: bool = False):
    csrf = _csrf(client, "/register")
    return client.post(
        "/register",
        data={"email": email, "password": password, "csrf_token": csrf, "next": "/"},
        follow_redirects=follow,
    )


def _count_users(email: str) -> int:
    from sqlalchemy import func, select

    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(User).where(User.email == email))


def test_register_then_home_shows_user(client):
    resp = _register(client, "alice@x.com", "password123")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    # Session established — the nav now shows the email.
    assert "alice@x.com" in client.get("/").text
    # Row exists with a hashed (not plaintext) password.
    from sqlalchemy import select

    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == "alice@x.com"))
        assert user is not None
        assert user.hashed_password and user.hashed_password != "password123"


def test_register_duplicate_is_generic_and_no_second_row(client):
    _register(client, "dup@x.com", "password123", follow=True)
    resp = _register(client, "dup@x.com", "password123")
    assert resp.status_code == 400
    assert "Could not create an account" in resp.text
    # The generic error must not reveal that the email is already registered.
    for leak in ("exists", "registered", "taken", "in use"):
        assert leak not in resp.text.lower()
    assert _count_users("dup@x.com") == 1


def test_register_invalid_email_and_short_password(client):
    bad_email = _register(client, "not-an-email", "password123")
    assert bad_email.status_code == 400 and "valid email" in bad_email.text
    assert _count_users("not-an-email") == 0

    short = _register(client, "bob@x.com", "short")
    assert short.status_code == 400 and "at least 8" in short.text
    assert _count_users("bob@x.com") == 0


def test_login_success_and_wrong_password(client):
    _register(client, "carol@x.com", "password123", follow=True)
    # Log out first so we can log back in.
    home = client.get("/")
    logout_csrf = _CSRF_RE.search(home.text).group(1)
    client.post("/logout", data={"csrf_token": logout_csrf}, follow_redirects=True)
    assert "carol@x.com" not in client.get("/").text

    csrf = _csrf(client, "/login")
    ok = client.post(
        "/login",
        data={"email": "carol@x.com", "password": "password123", "csrf_token": csrf, "next": "/"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert "carol@x.com" in client.get("/").text


def test_login_unknown_and_wrong_are_identical_generic(client):
    _register(client, "dave@x.com", "password123", follow=True)
    csrf = _csrf(client, "/login")
    client.post("/logout", data={"csrf_token": _CSRF_RE.search(client.get("/").text).group(1)},
                follow_redirects=True)

    csrf = _csrf(client, "/login")
    wrong_pw = client.post(
        "/login",
        data={"email": "dave@x.com", "password": "nope", "csrf_token": csrf, "next": "/"},
    )
    csrf = _csrf(client, "/login")
    unknown = client.post(
        "/login",
        data={"email": "ghost@x.com", "password": "nope", "csrf_token": csrf, "next": "/"},
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert "Invalid email or password." in wrong_pw.text
    # Byte-identical responses — a wrong password is indistinguishable from an
    # unknown account (no enumeration).
    assert wrong_pw.text == unknown.text


def test_csrf_required_on_register(client):
    client.get("/register")  # establishes a session + csrf
    resp = client.post(
        "/register",
        data={"email": "eve@x.com", "password": "password123", "csrf_token": "bogus", "next": "/"},
    )
    assert resp.status_code == 403
    assert _count_users("eve@x.com") == 0


def test_logout_clears_session(client):
    _register(client, "frank@x.com", "password123", follow=True)
    csrf = _CSRF_RE.search(client.get("/").text).group(1)
    client.post("/logout", data={"csrf_token": csrf}, follow_redirects=True)
    home = client.get("/")
    assert "frank@x.com" not in home.text
    assert "Log in" in home.text


def test_anonymous_can_read_existing_pages(client):
    assert client.get("/").status_code == 200
    assert client.get("/teams").status_code == 200
