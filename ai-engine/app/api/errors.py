from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorDetail, ErrorPayload, ErrorResponse
from app.auth.service import AuthServiceError
from app.config import LLMConfigurationError
from app.services.job_catalog import JobCatalogError
from app.services.matching.exceptions import (
    HybridMatchingError,
    InvalidMatchingInputError,
    JobCatalogUnavailableError,
    JobNotFoundError,
)

LOGGER = logging.getLogger("jobmatch.api")


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []
        self.headers = headers
        super().__init__(message)


class UploadValidationError(APIError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_upload",
        status_code: int = 415,
    ) -> None:
        super().__init__(
            status_code=status_code,
            code=code,
            message=message,
        )


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorPayload(
            code=code,
            message=message,
            details=details or [],
        ),
        request_id=_request_id(request),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return _response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details: list[ErrorDetail] = []
    for item in exc.errors()[:20]:
        loc = [str(part) for part in item.get("loc", ()) if str(part) != "body"]
        details.append(
            ErrorDetail(
                loc=loc,
                message=str(item.get("msg", "Invalid value"))[:1000],
                type=str(item.get("type", "validation_error"))[:120],
            )
        )
    return _response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed.",
        details=details,
    )


async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    codes = {404: "not_found", 405: "method_not_allowed"}
    message = exc.detail if isinstance(exc.detail, str) else "HTTP request failed."
    return _response(
        request,
        status_code=exc.status_code,
        code=codes.get(exc.status_code, "http_error"),
        message=str(message)[:2000],
        headers=dict(exc.headers or {}),
    )


async def auth_service_error_handler(
    request: Request, exc: AuthServiceError
) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return _response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=str(exc)[:2000],
        headers=headers,
    )


async def invalid_matching_input_handler(
    request: Request, exc: InvalidMatchingInputError
) -> JSONResponse:
    return _response(
        request,
        status_code=422,
        code="matching_input_error",
        message=str(exc)[:2000],
    )


async def job_not_found_handler(
    request: Request, exc: JobNotFoundError
) -> JSONResponse:
    return _response(
        request,
        status_code=404,
        code="job_not_found",
        message=str(exc)[:2000],
    )


async def catalog_unavailable_handler(
    request: Request, exc: JobCatalogError | JobCatalogUnavailableError
) -> JSONResponse:
    LOGGER.error("Job catalog unavailable: %s", type(exc).__name__)
    return _response(
        request,
        status_code=503,
        code="job_catalog_unavailable",
        message=(
            "The job catalog is unavailable. Verify JOBS_FEATURES_PATH and that the "
            "feature table has been generated."
        ),
    )


async def hybrid_matching_handler(
    request: Request, exc: HybridMatchingError
) -> JSONResponse:
    return _response(
        request,
        status_code=422,
        code="hybrid_matching_error",
        message=str(exc)[:2000],
    )


async def llm_configuration_handler(
    request: Request, exc: LLMConfigurationError
) -> JSONResponse:
    LOGGER.warning("LLM configuration unavailable: %s", type(exc).__name__)
    return _response(
        request,
        status_code=503,
        code="llm_configuration_error",
        message=(
            "The optional LLM provider is not configured. Local matching remains "
            "available without an OpenAI API key."
        ),
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    LOGGER.error("Unhandled API error: %s", type(exc).__name__)
    return _response(
        request,
        status_code=500,
        code="internal_error",
        message="An unexpected internal error occurred.",
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(  # type: ignore[arg-type]
        AuthServiceError, auth_service_error_handler
    )
    app.add_exception_handler(
        RequestValidationError, validation_error_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        StarletteHTTPException, http_error_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        InvalidMatchingInputError, invalid_matching_input_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(JobNotFoundError, job_not_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(JobCatalogError, catalog_unavailable_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        JobCatalogUnavailableError, catalog_unavailable_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        HybridMatchingError, hybrid_matching_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        LLMConfigurationError, llm_configuration_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, unexpected_error_handler)
