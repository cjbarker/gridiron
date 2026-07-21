"""Google SSO via Authlib (OpenID Connect discovery).

Enabled only when GOOGLE_CLIENT_ID/SECRET are configured; otherwise
:func:`get_oauth` returns None and the flow is unavailable (the UI hides the
"Continue with Google" button). See plan KTD5.
"""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuth
from sqlalchemy import select
from sqlalchemy.orm import Session

from gridiron.config import get_settings
from gridiron.db.models import OAuthAccount, User

_GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"


def get_oauth() -> OAuth | None:
    """An OAuth registry with Google registered, or None when unconfigured.

    Built per call (registration is cheap and network-free; provider metadata
    is fetched lazily) so it always reflects current settings.
    """
    s = get_settings()
    if not (s.google_client_id and s.google_client_secret):
        return None
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        server_metadata_url=_GOOGLE_METADATA,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


def find_or_create_google_user(db: Session, sub: str, email: str | None) -> User:
    """Resolve the Gridiron account for a Google identity.

    Order: (1) existing link by Google ``sub``; (2) existing account with the
    same verified email — link a new OAuthAccount to it; (3) create a new
    passwordless account. Commits and returns the user.
    """
    account = db.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == "google",
            OAuthAccount.provider_account_id == sub,
        )
    )
    if account is not None:
        return db.get(User, account.user_id)

    email_norm = email.lower() if email else None
    user = (
        db.scalar(select(User).where(User.email == email_norm)) if email_norm else None
    )
    if user is None:
        user = User(email=email_norm or f"google_{sub}@users.noreply", is_verified=True)
        db.add(user)
        db.flush()

    db.add(
        OAuthAccount(
            user_id=user.id, provider="google", provider_account_id=sub, email=email_norm
        )
    )
    db.commit()
    db.refresh(user)
    return user
