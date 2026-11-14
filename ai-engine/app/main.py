from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import get_api_settings
from app.api.errors import install_exception_handlers
from app.api.router import api_router
from app.api.routes import health
from app.auth.settings import get_auth_settings
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.deployment import validate_deployment_configuration
from app.core.logging import (
    configure_application_logging,
    configure_security_logging,
    install_request_id_log_attribute,
    log_operational_event,
)
from app.core.rate_limit import RATE_LIMITER, configure_rate_limiter
from app.core.request_context import (
    REQUEST_ID_HEADER,
    normalize_request_id,
    reset_request_id,
    set_request_id,
)
from app.db.init import init_database
from app.db.session import dispose_engines

ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
ALLOWED_HEADERS = [
    "Accept",
    "Authorization",
    "Content-Type",
    "X-CSRF-Token",
    "X-Request-ID",
]


def create_app() -> FastAPI:
    install_request_id_log_attribute()
    settings = get_api_settings()
    auth_settings = get_auth_settings()
    configure_application_logging(
        level=settings.log_level,
        json_logs=settings.log_json,
    )
    configure_security_logging()
    validate_deployment_configuration(settings, auth_settings)
    configure_rate_limiter(settings=settings)
    sensitive_path_prefixes = tuple(
        f"{settings.api_prefix}/{name}"
        for name in ("auth", "account", "cv", "matches", "resumes", "applications")
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        log_operational_event(
            "service_starting",
            environment=settings.app_env,
            version=settings.app_version,
            rate_limit_backend=settings.rate_limit_backend,
        )
        try:
            if settings.auto_create_tables:
                init_database()
            yield
        except Exception as exc:
            log_operational_event(
                "service_start_failed",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            log_operational_event(
                "service_stopping",
                environment=settings.app_env,
                version=settings.app_version,
            )
            RATE_LIMITER.close()
            dispose_engines()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "FastAPI wrapper around the JobMatch AI hybrid matching engine. "
            "Local matching is deterministic and does not require an OpenAI API key. "
            "Remote LLM explanations require both a per-request opt-in and the "
            "server-level ALLOW_REMOTE_LLM_ANALYSIS flag. Account, CV, and matching "
            "data require authentication."
        ),
        docs_url="/docs" if settings.enable_api_docs else None,
        redoc_url="/redoc" if settings.enable_api_docs else None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
        lifespan=lifespan,
        debug=settings.debug,
    )

    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        expose_headers=["Retry-After", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        if request_id is None:
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            log_operational_event(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return response
        except Exception as exc:
            log_operational_event(
                "request_failed",
                method=request.method,
                path=request.url.path,
                error_type=type(exc).__name__,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        finally:
            reset_request_id(token)

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "frame-ancestors 'none'",
        )
        path = request.url.path
        if any(
            path == prefix or path.startswith(f"{prefix}/")
            for prefix in sensitive_path_prefixes
        ):
            response.headers["Cache-Control"] = "no-store"
        if settings.enable_hsts:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    install_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
