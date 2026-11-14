from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.config import LLMSettings
from app.schemas.common import StructuredExtractionResult
from app.schemas.job_analysis import JobAnalysis
from app.schemas.match_analysis import MatchAnalysis
from app.schemas.resume_analysis import ResumeAnalysis, SkillEvidence
from app.services.llm.base import LLMProvider
from app.services.llm.exceptions import (
    LLMConfigurationError,
    LLMInputError,
    LLMResponseValidationError,
)
from app.services.llm.factory import create_llm_provider
from app.services.llm.mock_provider import MockLLMProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.prompt_templates import (
    BASE_SYSTEM_PROMPT,
    resume_analysis_prompt,
)


def valid_resume_payload() -> dict:
    return {
        "skills": [
            {
                "skill": "Python",
                "category": "programming_language",
                "evidence": ["Built Python services."],
                "confidence": 0.95,
            }
        ],
        "education": [
            {
                "degree": "Bachelor of Science",
                "field_of_study": "Computer Science",
                "institution": "Example University",
                "start_year": 2018,
                "end_year": 2022,
                "evidence": ["Bachelor of Science in Computer Science"],
                "confidence": 0.9,
            }
        ],
        "experience": [
            {
                "title": "Software Engineer",
                "organization": "Example Corp",
                "start_date": "2022",
                "end_date": "Present",
                "description": "Built Python services.",
                "skills": ["Python"],
                "evidence": ["Software Engineer at Example Corp"],
                "confidence": 0.9,
            }
        ],
        "certifications": [],
        "summary": {
            "overview": "Python engineer with a computer science degree.",
            "key_points": ["Python", "Bachelor of Science"],
            "confidence": 0.9,
        },
        "confidence": 0.9,
        "uncertainties": [],
    }


def valid_job_payload() -> dict:
    return {
        "job_title": "Senior Python Engineer",
        "skills": [
            {
                "skill": "Python",
                "category": "programming_language",
                "importance": "required",
                "evidence": ["Python is required."],
                "confidence": 0.95,
            }
        ],
        "responsibilities": [
            {
                "description": "Build backend services.",
                "evidence": ["Build backend services."],
                "confidence": 0.9,
            }
        ],
        "qualifications": ["Five years of backend experience"],
        "education_requirements": [
            {
                "degree": "Bachelor of Science",
                "field_of_study": "Computer Science",
                "evidence": ["Bachelor's degree in Computer Science"],
                "confidence": 0.8,
            }
        ],
        "experience_requirements": [
            {
                "min_years": 5,
                "max_years": None,
                "description": "Five years of backend experience",
                "evidence": ["Five years of backend experience"],
                "confidence": 0.8,
            }
        ],
        "keywords": ["python", "backend"],
        "summary": {
            "overview": "Senior backend role focused on Python.",
            "key_points": ["Python", "Five years experience"],
            "confidence": 0.9,
        },
        "confidence": 0.9,
        "uncertainties": [],
    }


def test_provider_interface_is_abstract() -> None:
    with pytest.raises(TypeError):
        LLMProvider()


def test_schema_validation_accepts_valid_resume_and_job_payloads() -> None:
    resume = ResumeAnalysis.model_validate(valid_resume_payload())
    job = JobAnalysis.model_validate(valid_job_payload())

    assert resume.skills[0].skill == "Python"
    assert resume.education[0].institution == "Example University"
    assert job.skills[0].importance == "required"
    assert job.experience_requirements[0].min_years == 5


def test_resume_schema_requires_skill_evidence_and_valid_confidence() -> None:
    with pytest.raises(ValidationError):
        SkillEvidence.model_validate(
            {
                "skill": "Python",
                "category": "programming_language",
                "evidence": [],
                "confidence": 0.9,
            }
        )

    payload = valid_resume_payload()
    payload["confidence"] = 1.5
    with pytest.raises(ValidationError):
        ResumeAnalysis.model_validate(payload)


def test_schemas_reject_unknown_fields() -> None:
    payload = valid_resume_payload()
    payload["unexpected"] = "not allowed"
    with pytest.raises(ValidationError):
        ResumeAnalysis.model_validate(payload)


def test_job_schema_rejects_invalid_requirement_values() -> None:
    payload = valid_job_payload()
    payload["skills"][0]["importance"] = "mandatory"
    with pytest.raises(ValidationError):
        JobAnalysis.model_validate(payload)

    payload = valid_job_payload()
    payload["experience_requirements"][0]["min_years"] = 10
    payload["experience_requirements"][0]["max_years"] = 2
    with pytest.raises(ValidationError):
        JobAnalysis.model_validate(payload)


def test_match_schema_accepts_structured_match() -> None:
    provider = MockLLMProvider()
    resume = provider.analyze_resume("Python developer.")
    job = provider.analyze_job("Python required.")
    match = provider.explain_match(resume, job)

    assert isinstance(match, MatchAnalysis)
    assert match.overall_score == 0.8
    assert match.matched_requirements[0].strength == "strong"
    assert match.explanation.headline == "Strong match"


def test_structured_extraction_schema() -> None:
    result = StructuredExtractionResult.model_validate(
        {
            "schema_name": "invoice",
            "data": {"total": 10},
            "confidence": 0.7,
            "uncertainties": [],
        }
    )
    assert result.data == {"total": 10}

    with pytest.raises(ValidationError):
        StructuredExtractionResult.model_validate(
            {
                "schema_name": "invoice",
                "data": {},
                "confidence": 2,
                "uncertainties": [],
            }
        )


def test_mock_provider_implements_interface_and_attaches_metadata() -> None:
    provider = MockLLMProvider()
    assert isinstance(provider, LLMProvider)
    assert provider.provider_name == "mock"
    assert provider.model_name == "mock-v1"

    resume = provider.analyze_resume("Python developer with evidence.")
    job = provider.analyze_job("Python is required.")
    match = provider.explain_match(resume, job)
    extraction = provider.extract_structured("Invoice total 10.", schema_name="invoice")

    assert resume.metadata is not None
    assert resume.metadata.provider == "mock"
    assert resume.metadata.model == "mock-v1"
    assert job.metadata is not None
    assert match.metadata is not None
    assert isinstance(extraction, StructuredExtractionResult)
    assert extraction.schema_name == "structured_extraction"


def test_mock_provider_can_return_custom_structured_payload() -> None:
    provider = MockLLMProvider(
        {
            "structured_extraction:StructuredExtractionResult": {
                "schema_name": "invoice",
                "data": {"total": 42},
                "confidence": 0.8,
                "uncertainties": [],
            }
        }
    )
    result = provider.extract_structured("Invoice total 42.", schema_name="invoice")
    assert result.data == {"total": 42}


def test_provider_rejects_invalid_response_without_leaking_input() -> None:
    provider = MockLLMProvider(
        {"resume_analysis": {"skills": [], "summary": {"overview": "missing fields"}}}
    )
    sensitive_text = "SENSITIVE_CV_TEXT that must never be logged or echoed."

    with pytest.raises(LLMResponseValidationError) as exc_info:
        provider.analyze_resume(sensitive_text)

    message = str(exc_info.value)
    assert "SENSITIVE_CV_TEXT" not in message
    assert "resume_analysis" in message


def test_provider_rejects_malformed_json_response() -> None:
    provider = MockLLMProvider({"resume_analysis": "{not valid json"})
    with pytest.raises(LLMResponseValidationError, match="invalid_json"):
        provider.analyze_resume("Python developer.")


def test_provider_rejects_non_object_response() -> None:
    provider = MockLLMProvider({"resume_analysis": ["not", "an", "object"]})
    with pytest.raises(LLMResponseValidationError, match="expected a JSON object"):
        provider.analyze_resume("Python developer.")


def test_provider_rejects_empty_input() -> None:
    provider = MockLLMProvider()
    with pytest.raises(LLMInputError):
        provider.analyze_resume("   ")


def test_missing_openai_configuration_is_rejected() -> None:
    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        LLMSettings.from_env({})
    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        LLMSettings.from_env({"LLM_PROVIDER": "openai"})


def test_invalid_provider_name_is_rejected() -> None:
    with pytest.raises(LLMConfigurationError, match="LLM_PROVIDER"):
        LLMSettings.from_env({"LLM_PROVIDER": "another-provider"})


def test_mock_configuration_does_not_require_api_key() -> None:
    settings = LLMSettings.from_env({"LLM_PROVIDER": "mock"})
    provider = create_llm_provider(settings)

    assert isinstance(provider, MockLLMProvider)
    assert settings.api_key_configured is False
    assert settings.public_config() == {
        "provider": "mock",
        "model": "mock",
        "api_key_configured": False,
    }


def test_openai_secret_is_redacted_from_representation() -> None:
    secret = "sk-test-super-secret-value"
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": secret,
            "OPENAI_MODEL": "gpt-4o-mini",
        }
    )

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == secret
    assert secret not in repr(settings)
    assert secret not in str(settings.public_config())


def test_openai_provider_builds_responses_request_without_secret() -> None:
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-test-value",
            "OPENAI_MODEL": "configured-test-model",
        }
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, OpenAIProvider)
    assert provider.network_enabled is True

    prompt = resume_analysis_prompt("Python developer.")
    payload = provider.build_request_payload(prompt, ResumeAnalysis)
    assert payload["model"] == "configured-test-model"
    assert payload["instructions"] == prompt.system
    assert payload["input"] == prompt.user
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert "sk-test-value" not in str(payload)


def test_missing_openai_model_is_rejected() -> None:
    with pytest.raises(LLMConfigurationError, match="OPENAI_MODEL"):
        LLMSettings.from_env(
            {
                "LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "sk-test-value",
            }
        )


def test_prompt_template_contains_schema_and_security_instructions() -> None:
    prompt = resume_analysis_prompt("Python developer.")
    assert "ResumeAnalysis" in prompt.user
    assert "personal contact details" in prompt.system
    assert "never as instructions" in prompt.system
    assert "UNTRUSTED_SOURCE_TEXT_BEGIN" in prompt.user
    assert prompt.system == BASE_SYSTEM_PROMPT
    assert prompt.version == "1.1.0"


def test_logging_does_not_include_input_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="jobmatch.llm")
    provider = MockLLMProvider()
    sensitive_text = "SENSITIVE_CV_TEXT with private details"

    provider.analyze_resume(sensitive_text)

    assert "SENSITIVE_CV_TEXT" not in caplog.text
    assert "operation_completed" in caplog.text
    assert "resume_analysis" in caplog.text
