from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.services.llm.base import BaseLLMProvider
from app.services.llm.exceptions import LLMProviderError
from app.services.llm.prompt_templates import PromptTemplate

DEFAULT_MOCK_RESPONSES: dict[str, Any] = {
    "resume_analysis": {
        "technical_skills": ["Python"],
        "programming_languages": ["Python"],
        "frameworks": [],
        "skills": [
            {
                "skill": "Python",
                "category": "programming_language",
                "basis": "explicit",
                "evidence": ["Built Python services."],
                "confidence": 0.95,
            }
        ],
        "education": [],
        "experience": [],
        "certifications": [],
        "summary": {
            "overview": "Mock résumé analysis.",
            "key_points": ["Python experience detected."],
            "confidence": 0.9,
        },
        "confidence": 0.9,
        "uncertainties": [],
    },
    "job_analysis": {
        "job_title": "Software Engineer",
        "mandatory_requirements": ["Python"],
        "preferred_requirements": [],
        "technical_skills": ["Python"],
        "skills": [
            {
                "skill": "Python",
                "category": "programming_language",
                "importance": "required",
                "basis": "explicit",
                "evidence": ["Python is required."],
                "confidence": 0.95,
            }
        ],
        "responsibilities": [],
        "qualifications": [],
        "education_requirements": [],
        "experience_requirements": [],
        "keywords": ["python"],
        "summary": {
            "overview": "Mock job analysis.",
            "key_points": ["Python is a required skill."],
            "confidence": 0.9,
        },
        "confidence": 0.9,
        "uncertainties": [],
    },
    "match_explanation": {
        "overall_score": 0.8,
        "summary": {
            "overview": "Mock match analysis.",
            "key_points": ["The candidate matches the primary skill requirement."],
            "confidence": 0.85,
        },
        "explanation": {
            "headline": "Strong match",
            "narrative": "The candidate demonstrates the main required skill.",
            "strengths": ["Python"],
            "gaps": [],
        },
        "matched_requirements": [
            {
                "requirement": "Python",
                "category": "programming_language",
                "resume_evidence": ["Built Python services."],
                "job_evidence": ["Python is required."],
                "strength": "strong",
                "confidence": 0.9,
            }
        ],
        "missing_requirements": [],
        "recommendations": [],
        "confidence": 0.85,
        "uncertainties": [],
    },
    "structured_extraction:StructuredExtractionResult": {
        "schema_name": "structured_extraction",
        "data": {},
        "confidence": 0.5,
        "uncertainties": [
            {
                "field": "data",
                "reason": "Mock provider returns an empty extraction.",
                "severity": "low",
            }
        ],
    },
}


class MockLLMProvider(BaseLLMProvider):
    """Deterministic provider for tests and local development without API calls."""

    provider_name = "mock"
    model_name = "mock-v1"

    def __init__(self, responses: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        self._responses = deepcopy(DEFAULT_MOCK_RESPONSES)
        if responses:
            self._responses.update(deepcopy(dict(responses)))

    def set_response(self, operation: str, payload: Any) -> None:
        self._responses[operation] = payload

    def _complete_json(
        self,
        operation: str,
        prompt: PromptTemplate,
        response_model: type[Any],
    ) -> Any:
        if operation not in self._responses:
            raise LLMProviderError(
                f"Mock provider has no response configured for operation '{operation}'."
            )
        return deepcopy(self._responses[operation])
