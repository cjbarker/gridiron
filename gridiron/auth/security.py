"""Security core: password hashing, sessions, current-user/role deps, CSRF.

Every auth flow (register/login/reset/OAuth/favorites/admin) builds on the
primitives here. Kept deliberately small and dependency-light.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from pwdlib import PasswordHash

from gridiron.db.models import User
from gridiron.db.session import get_db, get_session_factory

# Argon2id (with bcrypt backward-compat) via pwdlib — see plan KTD2.
_pwd = PasswordHash.recommended()


# --- password hashing -------------------------------------------------------

def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    """Verify a password; False for OAuth-only users (no stored hash)."""
    if not hashed:
        return False
    try:
        return _pwd.verify(password, hashed)
    except Exception:
        return False


def verify_and_upgrade(password: str, hashed: str | None) -> tuple[bool, str | None]:
    """Verify and, if the hash uses outdated params, return an upgraded hash.

    Returns ``(valid, new_hash_or_None)``. Callers persist ``new_hash`` when set.
    """
    if not hashed:
        return False, None
    try:
        return _pwd.verify_and_update(password, hashed)
    except Exception:
        return False, None


# --- password-reset token hashing (shared with U5) --------------------------

def hash_token(raw: str) -> str:
    """SHA-256 hex of a raw token; only the hash is ever persisted."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- sessions ---------------------------------------------------------------

def login_session(request: Request, user: User) -> None:
    """Establish an authenticated session, rotating the session id."""
    csrf = request.session.get("csrf_token")
    request.session.clear()
    request.session["user_id"] = user.id
    if csrf:
        request.session["csrf_token"] = csrf


def logout_session(request: Request) -> None:
    request.session.clear()


def load_user(session: Session, user_id: int | None) -> User | None:
    if not user_id:
        return None
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


# --- dependencies -----------------------------------------------------------

class RequiresLogin(Exception):
    """Raised by ``require_user`` when no active session; handled by a redirect."""

    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """The authenticated user, or None. Never raises — safe for optional auth."""
    return load_user(db, request.session.get("user_id"))


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = load_user(db, request.session.get("user_id"))
    if user is None:
        raise RequiresLogin(next_url=request.url.path)
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin access required")
    return user


# --- CSRF -------------------------------------------------------------------

def issue_csrf(request: Request) -> str:
    """Return the session's CSRF token, creating one on first use."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted: str | None) -> bool:
    token = request.session.get("csrf_token")
    return bool(token and submitted and hmac.compare_digest(token, submitted))


# --- template context -------------------------------------------------------

def template_user(request: Request) -> dict:
    """Jinja context processor: exposes ``user`` (or None) to every template.

    For authenticated users it also exposes ``csrf_token`` so shared chrome
    (e.g. the nav logout form in base.html) can post safely. Anonymous requests
    get no token, so no session cookie is created just by viewing a page.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return {"user": None}
    with get_session_factory()() as session:
        user = load_user(session, user_id)
        if user is None:
            return {"user": None}
        session.expunge(user)  # detach; columns stay loaded (expire_on_commit=False)
        return {"user": user, "csrf_token": issue_csrf(request)}
