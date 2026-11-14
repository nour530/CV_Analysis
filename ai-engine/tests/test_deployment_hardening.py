from __future__ import annotations

import secrets

import pytest
import fakeredis
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.errors import APIError
from app.api.settings import APIConfigurationError, APISettings
from app.api.routes import health
from app.core.deployment import validate_deployment_configuration
from app.core.rate_limit import (
    MemoryRateLimitBackend,
    RateLimitBackendError,
    RateLimitDecision,
    RateLimiter,
    RedisRateLimitBackend,
    RATE_LIMITER,
    enforce_rate_limit,
    make_rate_limit_key,
)
from app.core.user_quota import enforce_user_quota
from app.db.settings import DatabaseSettings


def _production_settings(**overrides: str) -> APISettings:
    values = {
        "APP_ENV": "production",
        "CORS_ORIGINS": "https://app.example.com",
        "ENABLE_HSTS": "true",
        "RATE_LIMIT_BACKEND": "memory",
    }
    values.update(overrides)
    return APISettings.from_env(values)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


class FailingBackend:
    name = "redis"

    def check(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        raise RateLimitBackendError("unavailable")

    def reset(self, key: str | None = None) -> None:
        return None

    def ping(self) -> bool:
        return False

    def close(self) -> None:
        return None


def test_rate_limit_backend_auto_selection() -> None:
    memory = APISettings.from_env({"RATE_LIMIT_BACKEND": "auto"})
    assert memory.rate_limit_backend == "memory"

    redis = APISettings.from_env(
        {
            "RATE_LIMIT_BACKEND": "auto",
            "REDIS_URL": "redis://localhost:6379/0",
        }
    )
    assert redis.rate_limit_backend == "redis"
    assert redis.redis_url == "redis://localhost:6379/0"


def test_production_auto_requires_redis() -> None:
    with pytest.raises(APIConfigurationError, match="REDIS_URL"):
        _production_settings(RATE_LIMIT_BACKEND="auto")


def test_production_explicit_memory_is_allowed() -> None:
    settings = _production_settings()
    assert settings.rate_limit_backend == "memory"
    assert settings.redis_url is None


def test_production_rejects_open_redis_failure_mode() -> None:
    with pytest.raises(APIConfigurationError, match="FAILURE_MODE"):
        _production_settings(RATE_LIMIT_REDIS_FAILURE_MODE="open")


def test_redis_url_scheme_is_validated() -> None:
    with pytest.raises(APIConfigurationError, match="REDIS_URL"):
        APISettings.from_env({"RATE_LIMIT_BACKEND": "redis"})
    with pytest.raises(APIConfigurationError, match="REDIS_URL"):
        APISettings.from_env(
            {
                "RATE_LIMIT_BACKEND": "redis",
                "REDIS_URL": "http://localhost:6379/0",
            }
        )


def test_production_logging_defaults_to_json() -> None:
    settings = _production_settings()
    assert settings.log_json is True
    assert APISettings.from_env({}).log_json is False


def test_cors_origin_paths_and_wildcards_are_rejected() -> None:
    with pytest.raises(APIConfigurationError, match="paths"):
        APISettings.from_env({"CORS_ORIGINS": "https://app.example.com/path"})
    with pytest.raises(APIConfigurationError, match="wildcard"):
        APISettings.from_env({"CORS_ORIGINS": "https://*.example.com"})


def test_database_settings_require_postgres_in_production() -> None:
    with pytest.raises(APIConfigurationError, match="DATABASE_URL"):
        DatabaseSettings.from_env({"APP_ENV": "production"})

    with pytest.raises(APIConfigurationError, match="PostgreSQL"):
        DatabaseSettings.from_env(
            {"APP_ENV": "production", "DATABASE_URL": "sqlite:///test.db"}
        )
    with pytest.raises(APIConfigurationError, match="SQLite or PostgreSQL"):
        DatabaseSettings.from_env(
            {"DATABASE_URL": "mysql://user:pass@localhost/jobmatch"}
        )


def test_database_pool_settings_are_applied() -> None:
    settings = DatabaseSettings.from_env(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+psycopg://user:pass@db/jobmatch",
            "DB_POOL_SIZE": "7",
            "DB_MAX_OVERFLOW": "3",
            "DB_POOL_TIMEOUT_SECONDS": "11",
            "DB_POOL_RECYCLE_SECONDS": "600",
            "DB_CONNECT_TIMEOUT_SECONDS": "4",
        }
    )
    assert settings.pool_size == 7
    assert settings.max_overflow == 3
    assert settings.pool_timeout_seconds == 11
    assert settings.pool_recycle_seconds == 600
    assert settings.connect_timeout_seconds == 4
    options = settings.engine_options()
    assert options["pool_size"] == 7
    assert options["max_overflow"] == 3
    assert options["connect_args"] == {"connect_timeout": 4}


def test_deployment_validation_rejects_sqlite_database() -> None:
    settings = APISettings.from_env(
        {
            "APP_ENV": "production",
            "CORS_ORIGINS": "https://app.example.com",
            "ENABLE_HSTS": "true",
            "RATE_LIMIT_BACKEND": "memory",
        }
    )
    from app.auth.settings import AuthSettings

    auth = AuthSettings.from_env(
        {
            "APP_ENV": "production",
            "JWT_SECRET_KEY": secrets.token_urlsafe(48),
            "COOKIE_SECURE": "true",
        }
    )
    with pytest.raises(APIConfigurationError, match="DATABASE_URL"):
        validate_deployment_configuration(settings, auth)


def test_shared_redis_backend_enforces_across_instances() -> None:
    server = fakeredis.FakeServer()
    first = RedisRateLimitBackend(
        fakeredis.FakeRedis(server=server),
        key_prefix="jobmatch:test:",
    )
    second = RedisRateLimitBackend(
        fakeredis.FakeRedis(server=server),
        key_prefix="jobmatch:test:",
    )

    assert first.check("shared", limit=2, window_seconds=60).allowed is True
    assert second.check("shared", limit=2, window_seconds=60).allowed is True
    denied = first.check("shared", limit=2, window_seconds=60)
    assert denied.allowed is False
    assert denied.retry_after >= 1

    first.reset("shared")
    assert second.check("shared", limit=2, window_seconds=60).allowed is True


def test_redis_backend_reset_all_and_ping() -> None:
    backend = RedisRateLimitBackend(
        fakeredis.FakeRedis(),
        key_prefix="jobmatch:test:",
    )
    assert backend.ping() is True
    assert backend.check("one", limit=1, window_seconds=60).allowed is True
    assert backend.check("two", limit=1, window_seconds=60).allowed is True
    backend.reset()
    assert backend.check("one", limit=1, window_seconds=60).allowed is True
    assert backend.check("two", limit=1, window_seconds=60).allowed is True
    backend.close()


def test_rate_limiter_failure_modes() -> None:
    closed = RateLimiter()
    closed.configure(backend=FailingBackend(), failure_mode="closed")
    with pytest.raises(RateLimitBackendError):
        closed.check("key", limit=1, window_seconds=60)

    opened = RateLimiter()
    opened.configure(backend=FailingBackend(), failure_mode="open")
    assert opened.check("key", limit=1, window_seconds=60).allowed is True


def test_enforce_rate_limit_returns_503_on_backend_failure() -> None:
    RATE_LIMITER.configure(backend=FailingBackend(), failure_mode="closed")
    with pytest.raises(APIError) as error:
        enforce_rate_limit(_request(), scope="test", limit=1, window_seconds=60)
    assert error.value.status_code == 503
    assert error.value.code == "rate_limit_unavailable"
    assert error.value.headers == {"Retry-After": "5"}


def test_user_quota_returns_503_on_backend_failure() -> None:
    RATE_LIMITER.configure(backend=FailingBackend(), failure_mode="closed")
    with pytest.raises(APIError) as error:
        enforce_user_quota(
            _request(),
            user_id="user-123",
            operation="cv_analysis",
            limit=1,
            window_seconds=60,
            settings=APISettings.from_env({}),
        )
    assert error.value.status_code == 503
    assert error.value.code == "usage_quota_unavailable"


def test_rate_limit_keys_are_pseudonymized() -> None:
    key = make_rate_limit_key("login-account", "person@example.com")
    assert "person@example.com" not in key
    assert key.startswith("login-account:")


def test_readiness_reports_memory_backend_disabled() -> None:
    response = Response()
    result = health.ready(response, APISettings.from_env({}))
    assert response.status_code == 200
    assert result.status == "ok"
    assert result.checks["redis"].status == "disabled"


def test_readiness_returns_503_when_database_unavailable(monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("database offline")

    monkeypatch.setattr(health, "get_engine", unavailable)
    response = Response()
    result = health.ready(response, APISettings.from_env({}))
    assert response.status_code == 503
    assert result.status == "unavailable"
    assert result.checks["database"].status == "unavailable"


def test_readiness_returns_503_when_redis_unavailable(monkeypatch) -> None:
    limiter = RateLimiter()
    limiter.configure(backend=FailingBackend(), failure_mode="closed")
    monkeypatch.setattr(health, "RATE_LIMITER", limiter)
    response = Response()
    result = health.ready(response, APISettings.from_env({}))
    assert response.status_code == 503
    assert result.status == "unavailable"
    assert result.checks["redis"].status == "unavailable"


def test_memory_backend_isolated_from_redis() -> None:
    memory = MemoryRateLimitBackend()
    assert memory.check("key", limit=1, window_seconds=60).allowed is True
    assert memory.check("key", limit=1, window_seconds=60).allowed is False


def test_health_routes_are_exposed() -> None:
    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health/live").status_code == 200
        assert client.get("/api/v1/health/live").status_code == 200

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        payload = ready.json()
        assert payload["status"] == "ok"
        assert payload["checks"]["database"]["status"] == "ok"
        assert payload["checks"]["redis"]["status"] == "disabled"

        versioned = client.get("/api/v1/health/ready")
        assert versioned.status_code == 200
