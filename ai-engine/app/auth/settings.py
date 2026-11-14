from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

from app.api.settings import (
    APIConfigurationError,
    AppEnvironment,
    read_app_environment,
)

ALLOWED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
ALLOWED_SAMESITE = frozenset({"strict", "lax", "none"})
PLACEHOLDER_JWT_SECRETS = frozenset(
    {
        "replace-with-a-random-secret-of-at-least-32-characters",
        "replace-with-a-long-random-secret",
    }
)
PLACEHOLDER_SECRET_MARKERS = (
    "replace-with",
    "replace_with",
    "change-me",
    "change_me",
    "changeme",
    "your-secret",
    "your_secret",
    "example-secret",
    "example_secret",
    "placeholder",
)
MIN_PRODUCTION_SECRET_UNIQUE_CHARACTERS = 12


def _is_placeholder_secret(secret: str) -> bool:
    normalized = secret.strip().casefold()
    return normalized in PLACEHOLDER_JWT_SECRETS or any(
        marker in normalized for marker in PLACEHOLDER_SECRET_MARKERS
    )


def _int_value(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise APIConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise APIConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


def _bool_value(
    env: Mapping[str, str],
    name: str,
    default: bool = False,
) -> bool:
    raw = (env.get(name) or "").strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise APIConfigurationError(f"{name} must be true or false.")


@dataclass(frozen=True, slots=True)
class AuthSettings:
    jwt_secret_key: str
    app_env: AppEnvironment = "development"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "jobmatch-ai"
    jwt_audience: str = "jobmatch-ai-web"
    access_token_seconds: int = 900
    refresh_token_seconds: int = 604_800
    session_seconds: int = 2_592_000
    cookie_secure: bool = False
    cookie_samesite: str = "strict"
    cookie_domain: str | None = None
    refresh_cookie_name: str = "jobmatch_refresh"
    csrf_cookie_name: str = "jobmatch_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    cookie_path: str = "/api/v1/auth"
    csrf_cookie_path: str = "/"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthSettings:
        env = os.environ if environ is None else environ
        app_env = read_app_environment(env)
        secret = (env.get("JWT_SECRET_KEY") or "").strip()
        if len(secret) < 32:
            raise APIConfigurationError(
                "JWT_SECRET_KEY must be set to at least 32 characters."
            )
        if app_env != "test" and _is_placeholder_secret(secret):
            raise APIConfigurationError(
                "JWT_SECRET_KEY must not use a documented placeholder value."
            )
        if (
            app_env == "production"
            and len(set(secret)) < MIN_PRODUCTION_SECRET_UNIQUE_CHARACTERS
        ):
            raise APIConfigurationError(
                "JWT_SECRET_KEY must have sufficient entropy in production."
            )

        algorithm = (env.get("JWT_ALGORITHM") or "HS256").strip().upper()
        if algorithm not in ALLOWED_JWT_ALGORITHMS:
            raise APIConfigurationError(
                "JWT_ALGORITHM must be one of HS256, HS384, or HS512."
            )

        access_seconds = _int_value(
            env,
            "ACCESS_TOKEN_SECONDS",
            900,
            minimum=60,
            maximum=3600,
        )
        refresh_seconds = _int_value(
            env,
            "REFRESH_TOKEN_SECONDS",
            604_800,
            minimum=300,
            maximum=2_592_000,
        )
        session_seconds = _int_value(
            env,
            "SESSION_SECONDS",
            2_592_000,
            minimum=refresh_seconds,
            maximum=7_776_000,
        )

        same_site = (env.get("COOKIE_SAMESITE") or "strict").strip().casefold()
        if same_site not in ALLOWED_SAMESITE:
            raise APIConfigurationError(
                "COOKIE_SAMESITE must be strict, lax, or none."
            )
        cookie_secure = _bool_value(env, "COOKIE_SECURE", False)
        if same_site == "none" and not cookie_secure:
            raise APIConfigurationError(
                "COOKIE_SAMESITE=none requires COOKIE_SECURE=true."
            )
        if app_env == "production" and not cookie_secure:
            raise APIConfigurationError(
                "COOKIE_SECURE must be true in production."
            )

        cookie_domain = (env.get("COOKIE_DOMAIN") or "").strip() or None
        issuer = (env.get("JWT_ISSUER") or "jobmatch-ai").strip()
        audience = (env.get("JWT_AUDIENCE") or "jobmatch-ai-web").strip()
        if not issuer or not audience:
            raise APIConfigurationError("JWT_ISSUER and JWT_AUDIENCE must not be empty.")

        return cls(
            jwt_secret_key=secret,
            app_env=app_env,
            jwt_algorithm=algorithm,
            jwt_issuer=issuer,
            jwt_audience=audience,
            access_token_seconds=access_seconds,
            refresh_token_seconds=refresh_seconds,
            session_seconds=session_seconds,
            cookie_secure=cookie_secure,
            cookie_samesite=same_site,
            cookie_domain=cookie_domain,
        )


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    return AuthSettings.from_env()
