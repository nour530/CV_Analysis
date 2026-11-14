from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request

from app.api.settings import APISettings, get_api_settings
from app.config import LLMSettings
from app.core.logging import get_llm_logger, log_llm_event
from app.core.rate_limit import enforce_rate_limit
from app.services.job_catalog import JobCatalog
from app.services.llm.base import LLMProvider
from app.services.llm.exceptions import LLMConfigurationError
from app.services.llm.factory import create_llm_provider
from app.services.matching.application import MatchingApplicationService
from app.services.matching.hybrid_service import HybridMatchingService
from app.services.matching.local_matcher import LocalMatcher


@lru_cache(maxsize=4)
def _catalog_for_path(path: str) -> JobCatalog:
    return JobCatalog.from_csv(path)


def enforce_global_rate_limit(
    request: Request,
    settings: APISettings = Depends(get_api_settings),
) -> None:
    if settings.api_rate_limit_requests <= 0:
        return
    enforce_rate_limit(
        request,
        scope="api-global",
        limit=settings.api_rate_limit_requests,
        window_seconds=settings.api_rate_limit_window_seconds,
        trusted_proxy_networks=settings.trusted_proxy_networks,
    )


def get_job_catalog(
    settings: APISettings = Depends(get_api_settings),
) -> JobCatalog:
    return _catalog_for_path(str(settings.jobs_features_path))


@lru_cache(maxsize=4)
def _hybrid_for_catalog(catalog: JobCatalog) -> HybridMatchingService:
    matcher = LocalMatcher(extractor=catalog.extractor, embedder=catalog.embedder)
    return HybridMatchingService(matcher=matcher, extractor=catalog.extractor)


def get_hybrid_matching_service(
    catalog: JobCatalog = Depends(get_job_catalog),
) -> HybridMatchingService:
    return _hybrid_for_catalog(catalog)


def get_matching_application_service(
    catalog: JobCatalog = Depends(get_job_catalog),
    hybrid_service: HybridMatchingService = Depends(get_hybrid_matching_service),
) -> MatchingApplicationService:
    return MatchingApplicationService(catalog, hybrid_service)


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider | None:
    """Return an optional provider; local matching never depends on it."""
    try:
        settings = LLMSettings.from_env()
        return create_llm_provider(settings)
    except LLMConfigurationError as exc:
        log_llm_event(
            get_llm_logger(),
            "provider_unavailable",
            error_type=type(exc).__name__,
        )
        return None
