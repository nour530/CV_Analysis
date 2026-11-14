from __future__ import annotations

from fastapi import Request

from app.api.errors import APIError
from app.api.settings import APISettings
from app.core.audit import audit_event
from app.core.rate_limit import (
    RATE_LIMITER,
    RateLimitBackendError,
    client_ip,
    make_rate_limit_key,
)

CV_ANALYSIS = "cv_analysis"
JOB_MATCH = "job_match"
RECOMMENDATIONS = "recommendations"
REMOTE_LLM = "remote_llm"
MATCHING_OPERATIONS = frozenset({JOB_MATCH, RECOMMENDATIONS})


def enforce_user_quota(
    request: Request,
    *,
    user_id: str,
    operation: str,
    limit: int,
    window_seconds: int,
    settings: APISettings,
) -> None:
    if limit <= 0:
        return

    try:
        decision = RATE_LIMITER.check(
            make_rate_limit_key(f"quota:{operation}", user_id),
            limit=limit,
            window_seconds=window_seconds,
        )
    except RateLimitBackendError as exc:
        audit_event(
            "security.quota.unavailable",
            "failure",
            request=request,
            user_id=user_id,
            client_ip=client_ip(request, settings.trusted_proxy_networks),
            metadata={"operation": operation},
        )
        raise APIError(
            status_code=503,
            code="usage_quota_unavailable",
            message=(
                "Usage protection is temporarily unavailable. Please try again later."
            ),
            headers={"Retry-After": "5"},
        ) from exc
    if decision.allowed:
        return

    metadata = {
        "operation": operation,
        "retry_after_seconds": decision.retry_after,
    }
    audit_event(
        "security.quota.rejected",
        "failure",
        request=request,
        user_id=user_id,
        client_ip=client_ip(request, settings.trusted_proxy_networks),
        metadata=metadata,
    )
    if operation in MATCHING_OPERATIONS:
        audit_event(
            "security.matching.repeated",
            "failure",
            request=request,
            user_id=user_id,
            client_ip=client_ip(request, settings.trusted_proxy_networks),
            metadata=metadata,
        )
    if operation == REMOTE_LLM:
        audit_event(
            "security.llm.quota.rejected",
            "failure",
            request=request,
            user_id=user_id,
            client_ip=client_ip(request, settings.trusted_proxy_networks),
            metadata=metadata,
        )
    raise APIError(
        status_code=429,
        code="usage_quota_exceeded",
        message="Usage limit exceeded. Please wait before trying again.",
        headers={"Retry-After": str(decision.retry_after)},
    )
