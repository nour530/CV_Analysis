from __future__ import annotations

import os

import pytest

from app.config import LLMSettings
from app.schemas.resume_analysis import ResumeAnalysis
from app.services.llm.factory import create_llm_provider

SYNTHETIC_CV = """
Synthetic Candidate
Software Engineer

Skills
Python, FastAPI, PostgreSQL, Git

Experience
Software Engineer, Synthetic Example Corp (2022-Present)
- Built Python and FastAPI services backed by PostgreSQL.
- Wrote automated tests for backend APIs.

Education
Bachelor of Science in Computer Science, Synthetic University, 2022

Certifications
Synthetic Cloud Practitioner
"""


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_OPENAI_INTEGRATION_TESTS", "").casefold() not in {"1", "true", "yes"}
    or not os.getenv("OPENAI_API_KEY")
    or not os.getenv("OPENAI_MODEL"),
    reason=(
        "Set RUN_OPENAI_INTEGRATION_TESTS=1, OPENAI_API_KEY and OPENAI_MODEL "
        "to run the optional live OpenAI test."
    ),
)
def test_live_openai_resume_analysis_uses_synthetic_data() -> None:
    settings = LLMSettings.from_env({**os.environ, "LLM_PROVIDER": "openai"})
    provider = create_llm_provider(settings)

    result = provider.analyze_resume(SYNTHETIC_CV)

    assert isinstance(result, ResumeAnalysis)
    assert result.summary.overview
    assert result.metadata is not None
    assert result.metadata.provider == "openai"
