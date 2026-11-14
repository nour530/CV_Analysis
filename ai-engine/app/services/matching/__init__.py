"""Deterministic local matching and optional LLM explanation orchestration."""

from app.services.matching.config import MatchingConfig
from app.services.matching.exceptions import HybridMatchingError
from app.services.matching.hybrid_service import HybridMatchingService
from app.services.matching.local_matcher import LocalMatcher, LocalMatchingError

__all__ = [
    "HybridMatchingError",
    "HybridMatchingService",
    "LocalMatcher",
    "LocalMatchingError",
    "MatchingConfig",
]
