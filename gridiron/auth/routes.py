"""Server-rendered auth flows: register, login, logout (U4).

Password reset (U5) and Google SSO (U6) add their routes to this same router.
All state-changing POSTs validate a session-bound CSRF token. Register/login
responses are deliberately generic to avoid leaking which emails have accounts.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from gridiron.auth import email as email_mod
from gridiron.auth import oauth as oauth_mod
from gridiron.auth import security
from gridiron.config import get_settings
from gridiron.db.models import PasswordResetToken, User, utcnow
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
def login_form(request: Request, next: str = "/", reset: int = 0):
    context = _auth_context(request, next, None)
    if reset:
        context["notice"] = "Your password has been reset. Please log in."
    return templates.TemplateResponse(request, "auth/login.html", context)


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


# --- password reset ---------------------------------------------------------

@router.get("/reset", response_class=HTMLResponse)
def reset_request_form(request: Request):
    return templates.TemplateResponse(
        request,
        "auth/reset_request.html",
        {"csrf_token": security.issue_csrf(request), "sent": False, "error": None},
    )


@router.post("/reset")
def reset_request(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    csrf_token: str = Form(""),
):
    if not security.verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")

    email_norm = normalized_email(email)
    if email_norm is not None:
        user = db.scalar(select(User).where(User.email == email_norm))
        if user is not None:
            raw = secrets.token_urlsafe(32)
            ttl = get_settings().password_reset_ttl_minutes
            db.add(
                PasswordResetToken(
                    user_id=user.id,
                    token_hash=security.hash_token(raw),
                    expires_at=utcnow() + timedelta(minutes=ttl),
                )
            )
            db.commit()
            link = f"{get_settings().base_url}/reset/confirm?token={raw}"
            email_mod.send_email(
                user.email,
                "Reset your Gridiron password",
                f"Someone requested a password reset for your account.\n\n"
                f"Reset it here (valid for {ttl} minutes):\n{link}\n\n"
                f"If you didn't request this, you can ignore this email.",
            )

    # Always the same response — never reveal whether the email has an account.
    return templates.TemplateResponse(
        request,
        "auth/reset_request.html",
        {"csrf_token": security.issue_csrf(request), "sent": True, "error": None},
    )


def _token_is_valid(db: Session, raw_token: str) -> bool:
    if not raw_token:
        return False
    row = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == security.hash_token(raw_token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > utcnow(),
        )
    )
    return row is not None


@router.get("/reset/confirm", response_class=HTMLResponse)
def reset_confirm_form(request: Request, token: str = "", db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "auth/reset_confirm.html",
        {
            "csrf_token": security.issue_csrf(request),
            "token": token,
            "valid": _token_is_valid(db, token),
            "error": None,
        },
    )


@router.post("/reset/confirm")
def reset_confirm(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(""),
    password: str = Form(...),
    csrf_token: str = Form(""),
):
    if not security.verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")

    def _render(error: str, valid: bool = True, status: int = 400):
        return templates.TemplateResponse(
            request,
            "auth/reset_confirm.html",
            {"csrf_token": security.issue_csrf(request), "token": token, "valid": valid,
             "error": error},
            status_code=status,
        )

    if len(password) < MIN_PASSWORD_LEN:
        return _render(f"Password must be at least {MIN_PASSWORD_LEN} characters.")

    # Atomically claim the token: only one caller can flip used_at from NULL,
    # which makes the reset single-use and race-safe (plan KTD6).
    now = utcnow()
    claimed = db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == security.hash_token(token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        db.rollback()
        return _render("This reset link is invalid or has expired.", valid=False)

    row = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == security.hash_token(token)
        )
    )
    user = db.get(User, row.user_id)
    user.hashed_password = security.hash_password(password)
    db.commit()

    # New password invalidates any pre-existing session for this user via the
    # session password-fingerprint binding (see security.user_from_request).
    security.logout_session(request)
    return RedirectResponse("/login?reset=1", status_code=303)


# --- Google SSO -------------------------------------------------------------

@router.get("/auth/google/login")
async def google_login(request: Request, next: str = "/"):
    oauth = oauth_mod.get_oauth()
    if oauth is None:
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    request.session["oauth_next"] = safe_next(next)
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    oauth = oauth_mod.get_oauth()
    if oauth is None:
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    try:
        token = await oauth.google.authorize_access_token(request)  # verifies state
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Google sign-in failed") from exc

    userinfo = token.get("userinfo") or {}
    sub = userinfo.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="Google sign-in failed")

    user = oauth_mod.find_or_create_google_user(db, sub, userinfo.get("email"))
    maybe_promote_admin(db, user)
    security.login_session(request, user)
    return RedirectResponse(request.session.pop("oauth_next", "/"), status_code=303)
