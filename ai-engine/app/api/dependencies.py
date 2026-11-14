from __future__ import annotations

import hashlib
import logging
import threading
import time
from functools import lru_cache

from fastapi import Depends, Request

from app.api.settings import APISettings, get_api_settings
from app.config import LLMSettings
from app.core.logging import get_llm_logger, log_llm_event
from app.core.rate_limit import enforce_rate_limit
from app.db.session import get_session_factory
from app.services.job_catalog import JobCatalog
from app.services.jobs.sync import active_postings
from app.services.llm.base import LLMProvider
from app.services.llm.exceptions import LLMConfigurationError
from app.services.llm.factory import create_llm_provider
from app.services.matching.application import MatchingApplicationService
from app.services.matching.hybrid_service import HybridMatchingService
from app.services.matching.local_matcher import LocalMatcher
from app.services.nlp.skills import LocalSkillExtractor

LOGGER = logging.getLogger("jobmatch.jobs")


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


@lru_cache(maxsize=1)
def _shared_skill_extractor() -> LocalSkillExtractor:
    return LocalSkillExtractor()


class _LiveCatalogCache:
    """Caches the catalog built from synchronised postings and refreshes it
    after ``JOBS_CATALOG_REFRESH_SECONDS``. A refresh only rebuilds the catalog
    (and its TF-IDF fit) when the set of active postings actually changed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.catalog: JobCatalog | None = None
        self.signature: str | None = None
        self.checked_at = 0.0

    def reset(self) -> None:
        with self._lock:
            self.catalog, self.signature, self.checked_at = None, None, 0.0

    def get(self, refresh_seconds: int) -> JobCatalog:
        now = time.monotonic()
        if self.catalog is not None and now - self.checked_at < refresh_seconds:
            return self.catalog
        with self._lock:
            if self.catalog is not None and now - self.checked_at < refresh_seconds:
                return self.catalog
            try:
                with get_session_factory()() as db:
                    rows = active_postings(db)
                    digest = hashlib.sha256(
                        "|".join(f"{row.id}:{row.content_hash}" for row in rows).encode()
                    ).hexdigest()
                    if self.catalog is None or digest != self.signature:
                        self.catalog = JobCatalog.from_postings(
                            rows, extractor=_shared_skill_extractor()
                        )
                        self.signature = digest
                        LOGGER.info("live_job_catalog_loaded jobs=%d", len(self.catalog))
            except Exception as exc:
                LOGGER.warning("live_job_catalog_unavailable %s", type(exc).__name__)
                if self.catalog is None:
                    self.catalog = JobCatalog([], extractor=_shared_skill_extractor())
            self.checked_at = now
            return self.catalog


_LIVE_CATALOG = _LiveCatalogCache()


def reset_job_catalog_cache() -> None:
    _LIVE_CATALOG.reset()


def get_job_catalog(
    settings: APISettings = Depends(get_api_settings),
) -> JobCatalog:
    """Return real synchronised jobs; ``auto`` falls back to the research
    dataset only while no postings have been synchronised yet."""
    if settings.jobs_catalog_source == "dataset":
        return _catalog_for_path(str(settings.jobs_features_path))
    live = _LIVE_CATALOG.get(settings.jobs_catalog_refresh_seconds)
    if settings.jobs_catalog_source == "auto" and len(live) == 0:
        return _catalog_for_path(str(settings.jobs_features_path))
    return live


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
