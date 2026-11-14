from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.auth.service import AuthContext, AuthService, AuthServiceError
from app.auth.settings import AuthSettings, get_auth_settings
from app.db.models import User
from app.db.session import get_db

_BEARER = HTTPBearer(auto_error=False)


def get_auth_service(
    db: Session = Depends(get_db),
    settings: AuthSettings = Depends(get_auth_settings),
) -> AuthService:
    return AuthService(db, settings)


def _unauthorized(exc: AuthServiceError) -> APIError:
    return APIError(
        status_code=exc.status_code,
        code=exc.code,
        message=str(exc),
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
    service: AuthService = Depends(get_auth_service),
) -> AuthContext:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise APIError(
            status_code=401,
            code="authentication_required",
            message="Authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return service.authenticate_access_token(credentials.credentials)
    except AuthServiceError as exc:
        raise _unauthorized(exc) from exc


def get_current_user(
    context: AuthContext = Depends(get_auth_context),
) -> User:
    return context.user
