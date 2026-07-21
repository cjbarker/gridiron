"""Integration tests for password reset (U5)."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
_TOKEN_RE = re.compile(r"/reset/confirm\?token=([\w\-]+)")


@pytest.fixture()
def sent_emails(monkeypatch):
    box: list[tuple[str, str, str]] = []

    class Recorder:
        def send(self, to, subject, body):
            box.append((to, subject, body))

    monkeypatch.setattr("gridiron.auth.email.get_email_sender", lambda: Recorder())
    return box


@pytest.fixture()
def client(db_env):
    from gridiron.api.main import app

    return TestClient(app)


def _csrf(client: TestClient, path: str) -> str:
    return _CSRF_RE.search(client.get(path).text).group(1)


def _make_user(email: str, password: str = "password123") -> None:
    from gridiron.auth.security import hash_password
    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        s.add(User(email=email, hashed_password=hash_password(password)))


def _password_hash(email: str) -> str | None:
    from sqlalchemy import select

    from gridiron.db.models import User
    from gridiron.db.session import session_scope

    with session_scope() as s:
        return s.scalar(select(User.hashed_password).where(User.email == email))


def _token_count() -> int:
    from sqlalchemy import func, select

    from gridiron.db.models import PasswordResetToken
    from gridiron.db.session import session_scope

    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(PasswordResetToken))


def _request_reset(client: TestClient, email: str):
    csrf = _csrf(client, "/reset")
    return client.post("/reset", data={"email": email, "csrf_token": csrf}, follow_redirects=False)


def test_reset_request_existing_email_sends_link(client, sent_emails):
    _make_user("user@x.com")
    resp = _request_reset(client, "user@x.com")
    assert resp.status_code == 200
    assert "we've sent a reset link" in resp.text
    assert _token_count() == 1
    assert len(sent_emails) == 1
    to, subject, body = sent_emails[0]
    assert to == "user@x.com" and _TOKEN_RE.search(body)


def test_reset_request_unknown_email_is_silent(client, sent_emails):
    resp = _request_reset(client, "ghost@x.com")
    assert resp.status_code == 200
    assert "we've sent a reset link" in resp.text  # identical generic response
    assert _token_count() == 0
    assert sent_emails == []


def _do_reset_flow(client, sent_emails, email, new_password):
    _request_reset(client, email)
    token = _TOKEN_RE.search(sent_emails[-1][2]).group(1)
    csrf = _csrf(client, f"/reset/confirm?token={token}")
    return token, client.post(
        "/reset/confirm",
        data={"token": token, "password": new_password, "csrf_token": csrf},
        follow_redirects=False,
    )


def test_reset_confirm_updates_password_and_consumes_token(client, sent_emails):
    _make_user("reset@x.com", "oldpassword1")
    old_hash = _password_hash("reset@x.com")
    token, resp = _do_reset_flow(client, sent_emails, "reset@x.com", "brandnewpass1")
    assert resp.status_code == 303 and resp.headers["location"] == "/login?reset=1"

    new_hash = _password_hash("reset@x.com")
    assert new_hash and new_hash != old_hash

    from gridiron.auth.security import verify_password

    assert verify_password("brandnewpass1", new_hash)
    assert not verify_password("oldpassword1", new_hash)


def test_reset_token_is_single_use(client, sent_emails):
    _make_user("single@x.com")
    token, first = _do_reset_flow(client, sent_emails, "single@x.com", "firstnewpass1")
    assert first.status_code == 303
    hash_after_first = _password_hash("single@x.com")

    # Re-using the same token must fail and must not change the password again.
    csrf = _csrf(client, "/reset")  # invalid-token confirm page has no form/csrf field
    second = client.post(
        "/reset/confirm",
        data={"token": token, "password": "secondnewpass1", "csrf_token": csrf},
    )
    assert second.status_code == 400
    assert "invalid or has expired" in second.text
    assert _password_hash("single@x.com") == hash_after_first


def test_reset_confirm_expired_token(client):
    from gridiron.auth.security import hash_token
    from gridiron.db.models import PasswordResetToken, User, utcnow
    from gridiron.db.session import session_scope

    with session_scope() as s:
        u = User(email="exp@x.com")
        s.add(u)
        s.flush()
        s.add(
            PasswordResetToken(
                user_id=u.id,
                token_hash=hash_token("rawexpired"),
                expires_at=utcnow() - timedelta(minutes=1),
            )
        )

    csrf = _csrf(client, "/reset")
    resp = client.post(
        "/reset/confirm",
        data={"token": "rawexpired", "password": "whateverpass1", "csrf_token": csrf},
    )
    assert resp.status_code == 400 and "invalid or has expired" in resp.text


def test_reset_confirm_unknown_token(client):
    csrf = _csrf(client, "/reset")
    resp = client.post(
        "/reset/confirm",
        data={"token": "nope", "password": "whateverpass1", "csrf_token": csrf},
    )
    assert resp.status_code == 400 and "invalid or has expired" in resp.text


def test_reset_confirm_requires_csrf(client, sent_emails):
    _make_user("csrf@x.com")
    _request_reset(client, "csrf@x.com")
    token = _TOKEN_RE.search(sent_emails[-1][2]).group(1)
    resp = client.post(
        "/reset/confirm",
        data={"token": token, "password": "brandnewpass1", "csrf_token": "bogus"},
    )
    assert resp.status_code == 403


def test_reset_invalidates_preexisting_session(client, sent_emails):
    # Session A: register + stay logged in.
    csrf = _csrf(client, "/register")
    client.post(
        "/register",
        data={"email": "sess@x.com", "password": "oldpassword1", "csrf_token": csrf, "next": "/"},
        follow_redirects=True,
    )
    assert "sess@x.com" in client.get("/").text

    # Session A requests the reset link...
    _request_reset(client, "sess@x.com")
    token = _TOKEN_RE.search(sent_emails[-1][2]).group(1)

    # ...but a *separate* session B completes the reset.
    other = TestClient(client.app)
    csrf_b = _csrf(other, f"/reset/confirm?token={token}")
    other.post(
        "/reset/confirm",
        data={"token": token, "password": "brandnewpass1", "csrf_token": csrf_b},
        follow_redirects=False,
    )

    # Session A is now invalidated by the password-fingerprint binding.
    assert "sess@x.com" not in client.get("/").text
    assert "Log in" in client.get("/").text
