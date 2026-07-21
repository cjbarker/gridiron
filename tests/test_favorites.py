"""Saved-favorites tests (U8)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from gridiron.ingest.pipeline import ingest_season

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture()
def client(db_env, fixture_source):
    # Seed the 2023 fixture so team/player pages render the favorite button.
    ingest_season(fixture_source, 2023)
    from gridiron.api.main import app

    return TestClient(app)


def _session_csrf(client: TestClient) -> str:
    return _CSRF_RE.search(client.get("/register").text).group(1)


def _register(client: TestClient, email: str = "fav@x.com"):
    csrf = _session_csrf(client)
    client.post(
        "/register",
        data={"email": email, "password": "password123", "csrf_token": csrf, "next": "/"},
        follow_redirects=True,
    )


def _add(client: TestClient, kind: str, ref: str, csrf: str | None = None):
    csrf = csrf or _CSRF_RE.search(client.get("/").text).group(1)
    return client.post(
        "/favorites/add",
        data={"kind": kind, "ref": ref, "csrf_token": csrf, "next": "/"},
        follow_redirects=False,
    )


def _fav_count(user_email: str) -> int:
    from sqlalchemy import func, select

    from gridiron.db.models import Favorite, User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        uid = s.scalar(select(User.id).where(User.email == user_email))
        return s.scalar(select(func.count()).select_from(Favorite).where(Favorite.user_id == uid))


def test_add_favorite_and_list(client):
    _register(client)
    assert _add(client, "team", "Georgia").status_code == 303
    assert _fav_count("fav@x.com") == 1
    page = client.get("/favorites")
    assert page.status_code == 200 and "Georgia" in page.text


def test_add_is_idempotent(client):
    _register(client)
    csrf = _CSRF_RE.search(client.get("/").text).group(1)
    _add(client, "team", "Alabama", csrf)
    _add(client, "team", "Alabama", csrf)
    assert _fav_count("fav@x.com") == 1


def test_remove_favorite(client):
    _register(client)
    csrf = _CSRF_RE.search(client.get("/").text).group(1)
    _add(client, "team", "Georgia", csrf)
    client.post(
        "/favorites/remove",
        data={"kind": "team", "ref": "Georgia", "csrf_token": csrf, "next": "/"},
        follow_redirects=False,
    )
    assert _fav_count("fav@x.com") == 0
    assert "Georgia" not in client.get("/favorites").text


def test_anonymous_add_redirects_to_login(client):
    resp = client.post(
        "/favorites/add",
        data={"kind": "team", "ref": "Georgia", "csrf_token": "x", "next": "/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_add_requires_csrf(client):
    _register(client)
    resp = client.post(
        "/favorites/add",
        data={"kind": "team", "ref": "Georgia", "csrf_token": "bogus", "next": "/"},
    )
    assert resp.status_code == 403
    assert _fav_count("fav@x.com") == 0


def test_favorite_button_hidden_for_anonymous(client):
    # A team page renders for anonymous users but shows no Save button.
    page = client.get("/teams/Georgia?season=2023")
    assert page.status_code == 200
    assert "/favorites/add" not in page.text
