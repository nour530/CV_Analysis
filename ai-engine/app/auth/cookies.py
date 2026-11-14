from __future__ import annotations

import hmac
import secrets

from fastapi import Request, Response

from app.api.errors import APIError
from app.auth.settings import AuthSettings


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookies(
    response: Response,
    *,
    refresh_token: str,
    csrf_token: str,
    settings: AuthSettings,
) -> None:
    common = {
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
    }
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_seconds,
        path=settings.cookie_path,
        httponly=True,
        **common,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=settings.refresh_token_seconds,
        path=settings.csrf_cookie_path,
        httponly=False,
        **common,
    )


def clear_auth_cookies(response: Response, settings: AuthSettings) -> None:
    common = {
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
    }
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.cookie_path,
        httponly=True,
        **common,
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path=settings.csrf_cookie_path,
        httponly=False,
        **common,
    )


def verify_csrf(request: Request, settings: AuthSettings) -> None:
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get(settings.csrf_header_name)
    if (
        not cookie_token
        or not header_token
        or not hmac.compare_digest(cookie_token, header_token)
    ):
        raise APIError(
            status_code=403,
            code="csrf_failed",
            message="The request could not be verified. Refresh the page and try again.",
        )
