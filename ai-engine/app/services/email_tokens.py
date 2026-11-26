"""Single-use tokens sent by email, for password reset and email verification.

The raw token goes only into the email link; the database keeps its SHA-256
hash, so a database leak exposes no usable link. A token is used at most once,
expires, and issuing a new one for the same purpose voids the older ones.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.tokens import hash_token
from app.db.models import EmailToken, User, utcnow

RESET = "reset"
VERIFY = "verify"
LIFETIMES = {RESET: timedelta(minutes=30), VERIFY: timedelta(hours=48)}


def _now():
    return utcnow().replace(tzinfo=None)


def issue(db: Session, user: User, purpose: str) -> str:
    """Create a token for `user`, void earlier unused ones, commit, and
    return the raw token for the email link."""
    now = _now()
    db.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw = secrets.token_urlsafe(32)
    db.add(EmailToken(user_id=user.id, purpose=purpose, token_hash=hash_token(raw), expires_at=now + LIFETIMES[purpose]))
    db.commit()
    return raw


def consume(db: Session, raw: str, purpose: str) -> User | None:
    """Mark the token used and return its active user, or None when the
    token is unknown, expired, already used, or for another purpose. The
    caller commits along with its own change."""
    if not raw or len(raw) > 200:
        return None
    now = _now()
    claimed = db.execute(
        update(EmailToken)
        .where(
            EmailToken.token_hash == hash_token(raw),
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
            EmailToken.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        return None
    user_id = db.scalar(select(EmailToken.user_id).where(EmailToken.token_hash == hash_token(raw)))
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        db.rollback()
        return None
    return user
