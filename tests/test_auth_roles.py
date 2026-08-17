"""Roles, admin area, and the gridiron-admin CLI (U7)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture()
def client(db_env):
    from gridiron.api.main import app

    return TestClient(app)


def _register(client: TestClient, email: str, password: str = "password123"):
    csrf = _CSRF_RE.search(client.get("/register").text).group(1)
    client.post(
        "/register",
        data={"email": email, "password": password, "csrf_token": csrf, "next": "/"},
        follow_redirects=True,
    )


def _set_role(email: str, role: str) -> None:
    from sqlalchemy import select

    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        s.scalar(select(User).where(User.email == email)).role = role


# --- CLI --------------------------------------------------------------------

def test_cli_promote_and_demote(db_env, capsys):
    from gridiron.auth.cli import main
    from gridiron.auth.security import hash_password
    from sqlalchemy import select

    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        s.add(User(email="cli@x.com", hashed_password=hash_password("password123")))

    assert main(["promote", "cli@x.com"]) == 0
    with session_scope() as s:
        assert s.scalar(select(User).where(User.email == "cli@x.com")).role == "admin"

    assert main(["demote", "cli@x.com"]) == 0
    with session_scope() as s:
        assert s.scalar(select(User).where(User.email == "cli@x.com")).role == "user"


def test_cli_unknown_email_nonzero(db_env):
    from gridiron.auth.cli import main

    assert main(["promote", "ghost@x.com"]) == 1


# --- admin route authorization ----------------------------------------------

def test_admin_route_allows_admin(client):
    _register(client, "admin@x.com")
    _set_role("admin@x.com", "admin")
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    assert "admin@x.com" in resp.text


def test_admin_route_forbids_regular_user(client):
    _register(client, "plain@x.com")  # default role "user"
    assert client.get("/admin/users").status_code == 403


def test_admin_route_redirects_anonymous(client):
    resp = client.get("/admin/users", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


# --- nav link visibility ----------------------------------------------------

def test_admin_nav_link_visibility(client):
    _register(client, "navadmin@x.com")
    assert "/admin/users" not in client.get("/").text  # regular user: hidden
    _set_role("navadmin@x.com", "admin")
    assert "/admin/users" in client.get("/").text  # admin: shown


# --- ADMIN_EMAILS auto-promotion --------------------------------------------

def test_admin_emails_auto_promote_on_register(client, monkeypatch):
    from gridiron import config

    monkeypatch.setenv("ADMIN_EMAILS", "boss@x.com")
    config.get_settings.cache_clear()
    try:
        _register(client, "boss@x.com")
        # The configured email is promoted; /admin/users is reachable.
        assert client.get("/admin/users").status_code == 200
        from sqlalchemy import select

        from gridiron.db.models import User
        from gridiron.db.session import session_scope

        with session_scope() as s:
            assert s.scalar(select(User.role).where(User.email == "boss@x.com")) == "admin"
    finally:
        config.get_settings.cache_clear()


def test_non_listed_email_stays_user(client, monkeypatch):
    from gridiron import config

    monkeypatch.setenv("ADMIN_EMAILS", "boss@x.com")
    config.get_settings.cache_clear()
    try:
        _register(client, "nobody@x.com")
        assert client.get("/admin/users").status_code == 403
    finally:
        config.get_settings.cache_clear()
