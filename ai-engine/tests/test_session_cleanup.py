from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuthSession, RefreshToken, User
from app.db.session import get_session_factory
from app.services.session_cleanup import cleanup_sessions

RETENTION = 30 * 24 * 60 * 60
NOW = datetime(2026, 1, 31, 12, 0, 0)


def _user(db) -> User:
    user = User(
        full_name="Cleanup User",
        email=f"cleanup-{uuid.uuid4().hex}@example.com",
        password_hash="not-used",
    )
    db.add(user)
    db.flush()
    return user


def _session(
    db,
    user: User,
    *,
    expires_delta: timedelta,
    revoked_delta: timedelta | None = None,
) -> AuthSession:
    session = AuthSession(
        user_id=user.id,
        created_at=NOW - timedelta(days=60),
        expires_at=NOW + expires_delta,
        revoked_at=NOW + revoked_delta if revoked_delta is not None else None,
    )
    db.add(session)
    db.flush()
    return session


def _token(
    db,
    session: AuthSession,
    *,
    expires_delta: timedelta,
    used_delta: timedelta | None = None,
    revoked_delta: timedelta | None = None,
) -> RefreshToken:
    token = RefreshToken(
        session_id=session.id,
        jti=str(uuid.uuid4()),
        token_hash="0" * 64,
        created_at=NOW - timedelta(days=60),
        expires_at=NOW + expires_delta,
        used_at=NOW + used_delta if used_delta is not None else None,
        revoked_at=NOW + revoked_delta if revoked_delta is not None else None,
    )
    db.add(token)
    db.flush()
    return token


def test_cleanup_deletes_only_records_past_retention() -> None:
    with get_session_factory()() as db:
        user = _user(db)
        expired = _session(db, user, expires_delta=-timedelta(days=31))
        revoked = _session(
            db,
            user,
            expires_delta=timedelta(days=20),
            revoked_delta=-timedelta(days=31),
        )
        recently_revoked = _session(
            db,
            user,
            expires_delta=-timedelta(days=31),
            revoked_delta=-timedelta(days=1),
        )
        active = _session(db, user, expires_delta=timedelta(days=20))

        expired_token = _token(db, expired, expires_delta=-timedelta(days=1))
        revoked_token = _token(db, revoked, expires_delta=timedelta(days=10))
        stale_used_token = _token(
            db,
            active,
            expires_delta=timedelta(days=10),
            used_delta=-timedelta(days=31),
        )
        active_token = _token(db, active, expires_delta=timedelta(days=10))
        db.commit()

        result = cleanup_sessions(
            db,
            session_retention_seconds=RETENTION,
            refresh_token_retention_seconds=RETENTION,
            now=NOW,
        )

        assert result.deleted_sessions == 2
        assert result.deleted_refresh_tokens == 3
        assert result.dry_run is False
        assert db.get(AuthSession, expired.id) is None
        assert db.get(AuthSession, revoked.id) is None
        assert db.get(AuthSession, recently_revoked.id) is not None
        assert db.get(AuthSession, active.id) is not None
        assert db.get(RefreshToken, expired_token.id) is None
        assert db.get(RefreshToken, revoked_token.id) is None
        assert db.get(RefreshToken, stale_used_token.id) is None
        assert db.get(RefreshToken, active_token.id) is not None

        second = cleanup_sessions(
            db,
            session_retention_seconds=RETENTION,
            refresh_token_retention_seconds=RETENTION,
            now=NOW,
        )
        assert second.deleted_sessions == 0
        assert second.deleted_refresh_tokens == 0


def test_cleanup_dry_run_reports_without_deleting() -> None:
    with get_session_factory()() as db:
        user = _user(db)
        expired = _session(db, user, expires_delta=-timedelta(days=31))
        _token(db, expired, expires_delta=-timedelta(days=1))
        db.commit()

        result = cleanup_sessions(
            db,
            session_retention_seconds=RETENTION,
            refresh_token_retention_seconds=RETENTION,
            now=NOW,
            dry_run=True,
        )

        assert result.dry_run is True
        assert result.deleted_sessions == 1
        assert result.deleted_refresh_tokens == 1
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert db.scalar(select(func.count()).select_from(RefreshToken)) == 1


def test_cleanup_failure_is_audited_and_reraised(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with get_session_factory()() as db:
        def _boom(*args, **kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(Session, "execute", _boom)

        with caplog.at_level(logging.INFO, logger="jobmatch.audit"):
            with pytest.raises(RuntimeError, match="database unavailable"):
                cleanup_sessions(
                    db,
                    session_retention_seconds=RETENTION,
                    refresh_token_retention_seconds=RETENTION,
                    now=NOW,
                )

    assert "security.cleanup.failed" in caplog.text
    assert "database unavailable" not in caplog.text


def test_cli_dry_run_reports_counts(capsys: pytest.CaptureFixture[str]) -> None:
    with get_session_factory()() as db:
        user = _user(db)
        expired = _session(db, user, expires_delta=-timedelta(days=31))
        _token(db, expired, expires_delta=-timedelta(days=1))
        db.commit()

    from app.cli import main

    exit_code = main(["cleanup-sessions", "--dry-run"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["dry_run"] is True
    assert output["deleted_sessions"] == 1
    assert output["deleted_refresh_tokens"] == 1

    with get_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert db.scalar(select(func.count()).select_from(RefreshToken)) == 1
