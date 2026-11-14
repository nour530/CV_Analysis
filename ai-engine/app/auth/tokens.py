from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from app.auth.settings import AuthSettings


class TokenError(Exception):
    def __init__(self, message: str, *, expired: bool = False) -> None:
        self.expired = expired
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    session_id: str
    jti: str
    token_type: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    claims: TokenClaims


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _encode_claims(
    *,
    subject: str,
    session_id: str,
    token_type: str,
    settings: AuthSettings,
    now: datetime,
    expires_at: datetime,
) -> IssuedToken:
    jti = str(uuid4())
    payload = {
        "sub": subject,
        "sid": session_id,
        "jti": jti,
        "type": token_type,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return IssuedToken(
        token=token,
        claims=TokenClaims(
            subject=subject,
            session_id=session_id,
            jti=jti,
            token_type=token_type,
            expires_at=expires_at,
        ),
    )


def create_access_token(
    *,
    user_id: str,
    session_id: str,
    settings: AuthSettings,
    now: datetime | None = None,
) -> IssuedToken:
    issued_at = now or utcnow()
    return _encode_claims(
        subject=user_id,
        session_id=session_id,
        token_type="access",
        settings=settings,
        now=issued_at,
        expires_at=issued_at + timedelta(seconds=settings.access_token_seconds),
    )


def create_refresh_token(
    *,
    user_id: str,
    session_id: str,
    settings: AuthSettings,
    expires_at: datetime,
    now: datetime | None = None,
) -> IssuedToken:
    issued_at = now or utcnow()
    return _encode_claims(
        subject=user_id,
        session_id=session_id,
        token_type="refresh",
        settings=settings,
        now=issued_at,
        expires_at=expires_at,
    )


def decode_token(
    token: str,
    *,
    expected_type: str,
    settings: AuthSettings,
) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={
                "require": [
                    "exp",
                    "iat",
                    "nbf",
                    "iss",
                    "aud",
                    "sub",
                    "sid",
                    "jti",
                    "type",
                ]
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired.", expired=True) from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Token is invalid.") from exc

    token_type = payload.get("type")
    if token_type != expected_type:
        raise TokenError("Token type is invalid.")

    subject = payload.get("sub")
    session_id = payload.get("sid")
    jti = payload.get("jti")
    expires = payload.get("exp")
    if not all(isinstance(value, str) and value for value in (subject, session_id, jti)):
        raise TokenError("Token claims are invalid.")
    if not isinstance(expires, (int, float)):
        raise TokenError("Token expiry is invalid.")

    return TokenClaims(
        subject=subject,
        session_id=session_id,
        jti=jti,
        token_type=token_type,
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc),
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches_hash(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)
