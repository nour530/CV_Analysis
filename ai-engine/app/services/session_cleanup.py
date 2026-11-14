from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import audit_event
from app.db.models import AuthSession, RefreshToken, utcnow


@dataclass(frozen=True, slots=True)
class SessionCleanupResult:
    deleted_sessions: int
    deleted_refresh_tokens: int
    session_cutoff: datetime
    token_cutoff: datetime
    dry_run: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "deleted_sessions": self.deleted_sessions,
            "deleted_refresh_tokens": self.deleted_refresh_tokens,
            "session_cutoff": self.session_cutoff.isoformat(),
            "token_cutoff": self.token_cutoff.isoformat(),
            "dry_run": self.dry_run,
        }


def _eligible_session_condition(session_cutoff: datetime):
    return or_(
        and_(
            AuthSession.revoked_at.is_not(None),
            AuthSession.revoked_at <= session_cutoff,
        ),
        and_(
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at <= session_cutoff,
        ),
    )


def _stale_token_condition(token_cutoff: datetime):
    return or_(
        RefreshToken.expires_at <= token_cutoff,
        and_(
            RefreshToken.used_at.is_not(None),
            RefreshToken.used_at <= token_cutoff,
        ),
        and_(
            RefreshToken.revoked_at.is_not(None),
            RefreshToken.revoked_at <= token_cutoff,
        ),
    )


def cleanup_sessions(
    db: Session,
    *,
    session_retention_seconds: int,
    refresh_token_retention_seconds: int,
    now: datetime | None = None,
    dry_run: bool = False,
) -> SessionCleanupResult:
    """Delete expired/revoked sessions and stale refresh tokens.

    Records are kept for a configurable retention window so recent revocations
    and refresh-token reuse evidence remain available for investigation. The
    operation is idempotent: a second run with the same clock and retention
    settings deletes nothing.
    """

    current = (now or utcnow()).replace(tzinfo=None)
    session_cutoff = current - timedelta(
        seconds=max(0, session_retention_seconds)
    )
    token_cutoff = current - timedelta(
        seconds=max(0, refresh_token_retention_seconds)
    )
    session_condition = _eligible_session_condition(session_cutoff)
    token_condition = _stale_token_condition(token_cutoff)
    eligible_session_ids = select(AuthSession.id).where(session_condition)
    token_delete_condition = or_(
        RefreshToken.session_id.in_(eligible_session_ids),
        token_condition,
    )

    try:
        if dry_run:
            deleted_tokens = (
                db.scalar(
                    select(func.count())
                    .select_from(RefreshToken)
                    .where(token_delete_condition)
                )
                or 0
            )
            deleted_sessions = (
                db.scalar(
                    select(func.count())
                    .select_from(AuthSession)
                    .where(session_condition)
                )
                or 0
            )
        else:
            deleted_tokens = (
                db.execute(delete(RefreshToken).where(token_delete_condition)).rowcount
                or 0
            )
            deleted_sessions = (
                db.execute(delete(AuthSession).where(session_condition)).rowcount
                or 0
            )
            db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        try:
            audit_event(
                "security.cleanup.failed",
                "failure",
                metadata={
                    "operation": "session_cleanup",
                    "reason": type(exc).__name__,
                },
            )
        except Exception:
            pass
        raise

    result = SessionCleanupResult(
        deleted_sessions=deleted_sessions,
        deleted_refresh_tokens=deleted_tokens,
        session_cutoff=session_cutoff,
        token_cutoff=token_cutoff,
        dry_run=dry_run,
    )
    try:
        audit_event(
            "security.cleanup.succeeded",
            "success",
            metadata={
                "operation": "session_cleanup",
                "deleted_sessions": result.deleted_sessions,
                "deleted_refresh_records": result.deleted_refresh_tokens,
                "session_cutoff": result.session_cutoff.isoformat(),
                "refresh_record_cutoff": result.token_cutoff.isoformat(),
                "dry_run": result.dry_run,
            },
        )
    except Exception:
        pass
    return result
