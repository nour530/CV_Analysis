"""A user's right to their data: export everything, or delete everything.

Export returns every record the app stores about the signed-in user, as JSON,
leaving out credentials (the password hash, session and refresh-token rows).

Deletion removes the user and every row that belongs to them in one
transaction: it all goes, or nothing does. Rows are deleted explicitly,
children first, rather than relying on ON DELETE CASCADE, because SQLite
(local development and tests) does not enforce foreign keys unless asked to.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    AuthSession,
    CandidateProfile,
    ChatMessage,
    CvDocumentRecord,
    CVAnalysis,
    JobApplication,
    RefreshToken,
    SavedMatch,
    User,
    UserSettings,
)

EXPORT_FORMAT_VERSION = 1
# Never exported: credentials, and internal ids that mean nothing to the user.
EXCLUDED_COLUMNS = {"password_hash", "token_hash"}

# Every table holding a user's data, in deletion order (children first).
USER_TABLES = (
    ChatMessage,
    JobApplication,
    CvDocumentRecord,
    SavedMatch,
    CVAnalysis,
    CandidateProfile,
    UserSettings,
)


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row(record: Any) -> dict[str, Any]:
    return {
        column.name: _plain(getattr(record, column.key))
        for column in record.__table__.columns
        if column.name not in EXCLUDED_COLUMNS
    }


def export_account(db: Session, user: User) -> dict[str, Any]:
    """Everything stored about `user`, for a data-access request."""
    data: dict[str, Any] = {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now().astimezone().isoformat(),
        "account": _row(user),
    }
    for model in reversed(USER_TABLES):
        rows = db.scalars(select(model).where(model.user_id == user.id)).all()
        data[model.__tablename__] = [_row(row) for row in rows]
    return data


def delete_account(db: Session, user_id: str) -> dict[str, int]:
    """Delete the user and all their data in one transaction. Returns the
    number of rows removed per table. On any error everything rolls back."""
    removed: dict[str, int] = {}
    try:
        session_ids = select(AuthSession.id).where(AuthSession.user_id == user_id)
        removed[RefreshToken.__tablename__] = db.execute(
            delete(RefreshToken).where(RefreshToken.session_id.in_(session_ids))
        ).rowcount
        removed[AuthSession.__tablename__] = db.execute(
            delete(AuthSession).where(AuthSession.user_id == user_id)
        ).rowcount
        for model in USER_TABLES:
            removed[model.__tablename__] = db.execute(delete(model).where(model.user_id == user_id)).rowcount
        removed[User.__tablename__] = db.execute(delete(User).where(User.id == user_id)).rowcount
        db.commit()
    except Exception:
        db.rollback()
        raise
    return removed
