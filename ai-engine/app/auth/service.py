from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.common_passwords import is_common_password
from app.auth.passwords import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    verify_password,
)
from app.auth.settings import AuthSettings
from app.auth.tokens import (
    TokenClaims,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    utcnow,
)
from app.db.models import AuthSession, RefreshToken, User, UserSettings, utcnow as db_utcnow


class AuthServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str,
        audit_reason: str | None = None,
        audit_user_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.audit_reason = audit_reason
        self.audit_user_id = audit_user_id
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: AuthSession
    claims: TokenClaims


class AuthService:
    def __init__(self, db: Session, settings: AuthSettings) -> None:
        self.db = db
        self.settings = settings

    @staticmethod
    def _reject_common_password(
        password: str,
        *,
        check_common_password: bool,
    ) -> None:
        if check_common_password and is_common_password(password):
            raise AuthServiceError(
                "Choose a less common password.",
                status_code=422,
                code="password_too_common",
                audit_reason="password_too_common",
            )

    def get_user_by_email(self, email: str) -> User | None:
        return self.db.scalar(
            select(User).where(User.email == email.strip().casefold())
        )

    def get_user(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    def register(
        self,
        *,
        full_name: str,
        email: str,
        password: str,
        check_common_password: bool = True,
    ) -> User | None:
        self._reject_common_password(
            password,
            check_common_password=check_common_password,
        )
        normalized_email = email.strip().casefold()
        password_hash = hash_password(password)
        if self.get_user_by_email(normalized_email) is not None:
            return None

        user = User(
            full_name=full_name.strip(),
            email=normalized_email,
            password_hash=password_hash,
            is_active=True,
        )
        try:
            self.db.add(user)
            self.db.flush()
            self.db.add(
                UserSettings(
                    user_id=user.id,
                    recommendation_limit=10,
                    candidate_pool_size=40,
                    preferred_category=None,
                    allow_remote_analysis=False,
                )
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return None
        return user

    def authenticate(self, *, email: str, password: str) -> User:
        user = self.get_user_by_email(email)
        password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        password_valid = verify_password(password, password_hash)
        if user is None or not password_valid or not user.is_active:
            disabled_attempt = user is not None and not user.is_active
            raise AuthServiceError(
                "Invalid email or password.",
                status_code=401,
                code="invalid_credentials",
                audit_reason=(
                    "account_disabled" if disabled_attempt else "invalid_credentials"
                ),
                audit_user_id=user.id if disabled_attempt else None,
            )
        return user

    def create_session(self, user: User) -> AuthTokens:
        now = utcnow()
        now_db = now.replace(tzinfo=None)
        session = AuthSession(
            user_id=user.id,
            created_at=now_db,
            expires_at=(now + timedelta(seconds=self.settings.session_seconds)).replace(
                tzinfo=None
            ),
        )
        self.db.add(session)
        self.db.flush()
        refresh_expiry = min(
            now + timedelta(seconds=self.settings.refresh_token_seconds),
            session.expires_at.replace(tzinfo=timezone.utc),
        )
        refresh = create_refresh_token(
            user_id=user.id,
            session_id=session.id,
            settings=self.settings,
            expires_at=refresh_expiry,
            now=now,
        )
        self.db.add(
            RefreshToken(
                session_id=session.id,
                jti=refresh.claims.jti,
                token_hash=hash_token(refresh.token),
                created_at=now_db,
                expires_at=refresh_expiry.replace(tzinfo=None),
            )
        )
        access = create_access_token(
            user_id=user.id,
            session_id=session.id,
            settings=self.settings,
            now=now,
        )
        self.db.commit()
        return AuthTokens(
            access_token=access.token,
            refresh_token=refresh.token,
            expires_in=self.settings.access_token_seconds,
        )

    def refresh_session(self, refresh_token: str) -> tuple[User, AuthTokens]:
        try:
            claims = decode_token(
                refresh_token,
                expected_type="refresh",
                settings=self.settings,
            )
        except TokenError as exc:
            raise AuthServiceError(
                "Your session has expired. Sign in again.",
                status_code=401,
                code="refresh_token_invalid",
            ) from exc

        now = utcnow()
        now_db = now.replace(tzinfo=None)
        claimed = self.db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.jti == claims.jti,
                RefreshToken.session_id == claims.session_id,
                RefreshToken.token_hash == hash_token(refresh_token),
                RefreshToken.used_at.is_(None),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now_db,
            )
            .values(used_at=now_db)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            token_record = self.db.scalar(
                select(RefreshToken).where(
                    RefreshToken.jti == claims.jti,
                    RefreshToken.session_id == claims.session_id,
                )
            )
            if token_record is None:
                raise AuthServiceError(
                    "Your session has expired. Sign in again.",
                    status_code=401,
                    code="refresh_token_invalid",
                )

            session = self.db.get(AuthSession, claims.session_id)
            if (
                session is None
                or session.revoked_at is not None
                or session.expires_at <= now_db
            ):
                raise AuthServiceError(
                    "Your session has expired. Sign in again.",
                    status_code=401,
                    code="session_expired",
                    audit_user_id=claims.subject,
                )

            self._revoke_session(session, now_db)
            self.db.commit()
            raise AuthServiceError(
                "Your session has expired. Sign in again.",
                status_code=401,
                code="refresh_token_reused",
                audit_reason="refresh_token_reused",
                audit_user_id=session.user_id,
            )

        session = self.db.get(AuthSession, claims.session_id)
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= now_db
        ):
            self.db.rollback()
            raise AuthServiceError(
                "Your session has expired. Sign in again.",
                status_code=401,
                code="session_expired",
                audit_user_id=claims.subject,
            )

        user = self.db.get(User, claims.subject)
        if user is None or not user.is_active or user.id != session.user_id:
            self._revoke_session(session, now_db)
            self.db.commit()
            raise AuthServiceError(
                "This account is not available.",
                status_code=401,
                code="account_unavailable",
                audit_reason="account_unavailable",
                audit_user_id=user.id if user is not None else claims.subject,
            )

        session.last_used_at = now_db
        refresh_expiry = min(
            now + timedelta(seconds=self.settings.refresh_token_seconds),
            session.expires_at.replace(tzinfo=timezone.utc),
        )
        new_refresh = create_refresh_token(
            user_id=user.id,
            session_id=session.id,
            settings=self.settings,
            expires_at=refresh_expiry,
            now=now,
        )
        self.db.add(
            RefreshToken(
                session_id=session.id,
                jti=new_refresh.claims.jti,
                token_hash=hash_token(new_refresh.token),
                created_at=now_db,
                expires_at=refresh_expiry.replace(tzinfo=None),
            )
        )
        access = create_access_token(
            user_id=user.id,
            session_id=session.id,
            settings=self.settings,
            now=now,
        )
        self.db.commit()
        return user, AuthTokens(
            access_token=access.token,
            refresh_token=new_refresh.token,
            expires_in=self.settings.access_token_seconds,
        )

    def authenticate_access_token(self, access_token: str) -> AuthContext:
        try:
            claims = decode_token(
                access_token,
                expected_type="access",
                settings=self.settings,
            )
        except TokenError as exc:
            raise AuthServiceError(
                "Authentication is required.",
                status_code=401,
                code="access_token_expired" if exc.expired else "access_token_invalid",
            ) from exc

        session = self.db.get(AuthSession, claims.session_id)
        now_db = db_utcnow()
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= now_db
        ):
            raise AuthServiceError(
                "Authentication is required.",
                status_code=401,
                code="session_expired",
            )

        user = self.db.get(User, claims.subject)
        if user is None or not user.is_active or user.id != session.user_id:
            raise AuthServiceError(
                "Authentication is required.",
                status_code=401,
                code="account_unavailable",
            )
        return AuthContext(user=user, session=session, claims=claims)

    def revoke_current_session(self, session_id: str) -> bool:
        session = self.db.get(AuthSession, session_id)
        if session is not None and session.revoked_at is None:
            self._revoke_session(session, db_utcnow())
            self.db.commit()
            return True
        return False

    def revoke_refresh_token(self, refresh_token: str) -> bool:
        try:
            claims = decode_token(
                refresh_token,
                expected_type="refresh",
                settings=self.settings,
            )
        except TokenError:
            return False
        return self.revoke_current_session(claims.session_id)

    def change_password(
        self,
        *,
        user: User,
        current_session_id: str,
        current_password: str,
        new_password: str,
        check_common_password: bool = True,
    ) -> int:
        if not verify_password(current_password, user.password_hash):
            raise AuthServiceError(
                "Current password is incorrect.",
                status_code=401,
                code="invalid_current_password",
            )
        self._reject_common_password(
            new_password,
            check_common_password=check_common_password,
        )
        user.password_hash = hash_password(new_password)
        user.updated_at = db_utcnow()
        now = db_utcnow()
        sessions = self.db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.id != current_session_id,
                AuthSession.revoked_at.is_(None),
            )
        ).all()
        for session in sessions:
            self._revoke_session(session, now)
        self.db.commit()
        return len(sessions)

    def update_profile(self, *, user: User, full_name: str) -> User:
        user.full_name = full_name.strip()
        user.updated_at = db_utcnow()
        self.db.commit()
        return user

    @staticmethod
    def _revoke_session(session: AuthSession, now: datetime) -> None:
        session.revoked_at = now
        session.refresh_tokens
        for token in session.refresh_tokens:
            if token.revoked_at is None:
                token.revoked_at = now
