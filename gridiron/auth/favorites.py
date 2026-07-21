"""Saved favorites: authenticated users save teams and players (U8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from gridiron.analytics import queries as q
from gridiron.auth import security
from gridiron.db.models import Favorite, User
from gridiron.db.session import get_db
from gridiron.templating import templates

router = APIRouter(tags=["favorites"])

KINDS = {"team", "player"}


def is_favorited(db: Session, user: User | None, kind: str, ref: str) -> bool:
    if user is None:
        return False
    return db.scalar(
        select(Favorite.id).where(
            Favorite.user_id == user.id, Favorite.kind == kind, Favorite.ref == str(ref)
        )
    ) is not None


def _validate(kind: str) -> None:
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="invalid favorite kind")


@router.post("/favorites/add")
def add_favorite(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(security.require_user),
    kind: str = Form(...),
    ref: str = Form(...),
    csrf_token: str = Form(""),
    next: str = Form("/"),
):
    if not security.verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")
    _validate(kind)
    if not is_favorited(db, user, kind, ref):
        db.add(Favorite(user_id=user.id, kind=kind, ref=ref))
        db.commit()
    return RedirectResponse(security.safe_next(next), status_code=303)


@router.post("/favorites/remove")
def remove_favorite(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(security.require_user),
    kind: str = Form(...),
    ref: str = Form(...),
    csrf_token: str = Form(""),
    next: str = Form("/"),
):
    if not security.verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")
    _validate(kind)
    db.execute(
        delete(Favorite).where(
            Favorite.user_id == user.id, Favorite.kind == kind, Favorite.ref == ref
        )
    )
    db.commit()
    return RedirectResponse(security.safe_next(next), status_code=303)


@router.get("/favorites", response_class=HTMLResponse)
def favorites_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(security.require_user),
):
    rows = (
        db.execute(
            select(Favorite).where(Favorite.user_id == user.id).order_by(Favorite.kind, Favorite.ref)
        )
        .scalars()
        .all()
    )
    teams = [f.ref for f in rows if f.kind == "team"]
    players = []
    for f in (r for r in rows if r.kind == "player"):
        profile = q.player_profile(db, int(f.ref)) if f.ref.isdigit() else None
        players.append({"id": f.ref, "name": profile["name"] if profile else f"Player #{f.ref}"})
    return templates.TemplateResponse(
        request, "favorites.html", {"teams": teams, "players": players}
    )
