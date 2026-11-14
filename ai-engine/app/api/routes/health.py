from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from app.api.dependencies import get_api_settings
from app.api.schemas import (
    DependencyStatus,
    HealthResponse,
    ReadinessResponse,
)
from app.api.settings import APISettings
from app.core.logging import log_operational_event
from app.core.rate_limit import RATE_LIMITER
from app.db.session import get_engine

router = APIRouter(tags=["Health"])


def _liveness(settings: APISettings) -> HealthResponse:
    return HealthResponse(service=settings.app_name, version=settings.app_version)


def _database_status() -> DependencyStatus:
    started = time.monotonic()
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        log_operational_event(
            "dependency_unavailable",
            dependency="database",
            error_type=type(exc).__name__,
        )
        return DependencyStatus(
            status="unavailable",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return DependencyStatus(
        status="ok",
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def _redis_status() -> DependencyStatus:
    if RATE_LIMITER.backend_name == "memory":
        return DependencyStatus(status="disabled")
    started = time.monotonic()
    if not RATE_LIMITER.ping():
        log_operational_event(
            "dependency_unavailable",
            dependency="redis",
            error_type="connection_error",
        )
        return DependencyStatus(
            status="unavailable",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return DependencyStatus(
        status="ok",
        latency_ms=int((time.monotonic() - started) * 1000),
    )


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
def health(settings: APISettings = Depends(get_api_settings)) -> HealthResponse:
    return _liveness(settings)


@router.get("/health/live", response_model=HealthResponse, summary="Liveness check")
def live(settings: APISettings = Depends(get_api_settings)) -> HealthResponse:
    return _liveness(settings)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
    summary="Readiness check",
)
def ready(
    response: Response,
    settings: APISettings = Depends(get_api_settings),
) -> ReadinessResponse:
    checks = {
        "database": _database_status(),
        "redis": _redis_status(),
    }
    available = all(check.status != "unavailable" for check in checks.values())
    response.status_code = 200 if available else 503
    return ReadinessResponse(
        status="ok" if available else "unavailable",
        service=settings.app_name,
        version=settings.app_version,
        checks=checks,
    )
