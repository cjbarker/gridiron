"""Server-rendered auth flows: register, login, logout (U4).

Password reset (U5) and Google SSO (U6) add their routes to this same router.
All state-changing POSTs validate a session-bound CSRF token. Register/login
responses are deliberately generic to avoid leaking which emails have accounts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from gridiron.auth import security
from gridiron.config import get_settings
from gridiron.db.models import User
from gridiron.db.session import get_db
from gridiron.templating import templates

router = APIRouter(tags=["auth"])

MIN_PASSWORD_LEN = 8
_email_adapter = TypeAdapter(EmailStr)


# --- helpers ----------------------------------------------------------------

def google_enabled() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret)


def normalized_email(raw: str) -> str | None:
    """Return the lowercased, validated email, or None if invalid."""
    try:
        return _email_adapter.validate_python((raw or "").strip()).lower()
    except ValidationError:
        return None


def safe_next(target: str | None) -> str:
    """Only permit local redirects — blocks open-redirect via ``next``."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


def maybe_promote_admin(db: Session, user: User) -> None:
    """Auto-promote configured ADMIN_EMAILS on login (convenience path)."""
    if user.role != "admin" and user.email in get_settings().admin_email_set():
        user.role = "admin"
        db.commit()


def _auth_context(request: Request, next_url: str, error: str | None) -> dict:
    return {
        "csrf_token": security.issue_csrf(request),
        "next": next_url,
        "error": error,
        "google_enabled": google_enabled(),
    }


# --- register ---------------------------------------------------------------

@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request, "auth/register.html", _auth_context(request, next, None)
    )


@router.post("/register")
def register(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    next: str = Form("/"),
):
    if not security.verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")

    email_norm = normalized_email(email)
    error: str | None = None
    if email_norm is None:
        error = "Enter a valid email address."
    elif len(password) < MIN_PASSWORD_LEN:
        error = f"Password must be at least {MIN_PASSWORD_LEN} characters."

    if error is None:
        exists = db.scalar(select(User).where(User.email == email_norm))
        if exists is not None:
            # Generic message — do not confirm the email is already registered.
            error = "Could not create an account with those details."
        else:
            user = User(email=email_norm, hashed_password=security.hash_password(password))
            db.add(user)
            db.commit()
            db.refresh(user)
            maybe_promote_admin(db, user)
            security.login_session(request, user)
            return RedirectResponse(safe_next(next), status_code=303)

    return templates.TemplateResponse(
        request, "auth/register.html", _auth_context(request, next, error), status_code=400
    )


# --- login / logout ---------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request, "auth/login.html", _auth_context(request, next, None)
    )


@router.post("/login")
def login(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    next: str = Form("/"),
):
    if not security.verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")

    email_norm = (email or "").strip().lower()
    user = db.scalar(select(User).where(User.email == email_norm))
    valid, new_hash = security.verify_and_upgrade(
        password, user.hashed_password if user else None
    )
    if user is not None and valid:
        if new_hash:
            user.hashed_password = new_hash
            db.commit()
        maybe_promote_admin(db, user)
        security.login_session(request, user)
        return RedirectResponse(safe_next(next), status_code=303)

    # Generic error — identical for unknown email and wrong password.
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        _auth_context(request, next, "Invalid email or password."),
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    if not security.verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")
    security.logout_session(request)
    return RedirectResponse("/", status_code=303)
