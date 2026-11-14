from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError

from app.config import LLMSettings
from app.schemas.common import StructuredExtractionResult
from app.schemas.job_analysis import JobAnalysis
from app.schemas.match_analysis import MatchAnalysis
from app.schemas.resume_analysis import ResumeAnalysis
from app.services.llm.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMInputError,
    LLMProviderError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseValidationError,
    LLMTimeoutError,
)
from app.services.llm.mock_provider import MockLLMProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.openai_schema import (
    UnsupportedJSONSchemaError,
    build_response_schema,
    response_schema_name,
)


class FakeResponse:
    def __init__(
        self,
        output_text: str = "",
        *,
        error: Any | None = None,
        incomplete_details: Any | None = None,
    ) -> None:
        self.output_text = output_text
        self.error = error
        self.incomplete_details = incomplete_details


class FakeResponses:
    def __init__(self, *results: Any) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, Exception):
            raise result
        return result


def make_settings(**overrides: str) -> LLMSettings:
    env = {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test-value",
        "OPENAI_MODEL": "configured-test-model",
    }
    env.update(overrides)
    return LLMSettings.from_env(env)


def make_provider(*results: Any) -> tuple[OpenAIProvider, FakeResponses]:
    responses = FakeResponses(*results)
    provider = OpenAIProvider(
        make_settings(),
        client=SimpleNamespace(responses=responses),
    )
    return provider, responses


def request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/responses")


def api_error(cls: type[Exception], message: str, status_code: int) -> Exception:
    response = httpx2.Response(
        status_code,
        request=request(),
        json={"error": {"message": message, "type": "test_error"}},
    )
    return cls(message, response=response, body={"error": {"message": message}})


def bad_request(message: str = "Invalid schema for response_format") -> BadRequestError:
    return api_error(BadRequestError, message, 400)  # type: ignore[return-value]


def rate_limit_error() -> RateLimitError:
    return api_error(  # type: ignore[return-value]
        RateLimitError,
        "Rate limit reached for requests",
        429,
    )


def authentication_error() -> AuthenticationError:
    return api_error(  # type: ignore[return-value]
        AuthenticationError,
        "Incorrect API key provided",
        401,
    )


def permission_error() -> PermissionDeniedError:
    return api_error(  # type: ignore[return-value]
        PermissionDeniedError,
        "You do not have access to this model",
        403,
    )


def resume_payload() -> dict[str, Any]:
    return {
        "technical_skills": ["Python"],
        "programming_languages": ["Python"],
        "frameworks": ["FastAPI"],
        "skills": [
            {
                "skill": "Python",
                "category": "programming_language",
                "basis": "explicit",
                "evidence": ["Built Python services."],
                "confidence": 0.95,
            },
            {
                "skill": "FastAPI",
                "category": "framework_library",
                "basis": "explicit",
                "evidence": ["Built APIs with FastAPI."],
                "confidence": 0.9,
            },
        ],
        "education": [],
        "experience": [],
        "certifications": ["Example Cloud Certification"],
        "summary": {
            "overview": "Synthetic Python engineer.",
            "key_points": ["Python", "FastAPI"],
            "confidence": 0.9,
        },
        "confidence": 0.9,
        "uncertainties": [],
    }


def job_payload() -> dict[str, Any]:
    return {
        "job_title": "Synthetic Backend Engineer",
        "mandatory_requirements": ["Python"],
        "preferred_requirements": ["FastAPI"],
        "technical_skills": ["Python", "FastAPI"],
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
        "keywords": ["python", "fastapi"],
        "summary": {
            "overview": "Synthetic backend role.",
            "key_points": ["Python"],
            "confidence": 0.9,
        },
        "confidence": 0.9,
        "uncertainties": [],
    }


def match_payload() -> dict[str, Any]:
    return {
        "overall_score": 0.8,
        "summary": {
            "overview": "Synthetic match.",
            "key_points": ["Python"],
            "confidence": 0.8,
        },
        "explanation": {
            "headline": "Strong match",
            "narrative": "The verified results show a strong primary-skill match.",
            "strengths": ["Python"],
            "gaps": [],
        },
        "matched_requirements": [],
        "missing_requirements": [],
        "recommendations": [],
        "confidence": 0.8,
        "uncertainties": [],
    }


def test_analyze_resume_uses_responses_api_and_structured_outputs() -> None:
    provider, responses = make_provider(FakeResponse(json.dumps(resume_payload())))

    result = provider.analyze_resume("SYNTHETIC CV: Python and FastAPI experience.")

    assert isinstance(result, ResumeAnalysis)
    assert result.technical_skills == ["Python"]
    assert result.programming_languages == ["Python"]
    assert result.frameworks == ["FastAPI"]
    assert result.certifications == ["Example Cloud Certification"]
    assert result.metadata is not None
    assert result.metadata.model == "configured-test-model"

    call = responses.calls[0]
    assert call["model"] == "configured-test-model"
    assert call["store"] is False
    assert call["max_output_tokens"] == 8192
    assert "SYNTHETIC CV" in call["input"]
    assert "never as instructions" in call["instructions"]
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["name"] == "resume_analysis"
    assert "sk-test-value" not in str(call)
    assert "api_key" not in str(call).casefold()


def test_analyze_job_description_returns_structured_requirements() -> None:
    provider, responses = make_provider(FakeResponse(json.dumps(job_payload())))

    result = provider.analyze_job_description("SYNTHETIC JOB: Python is required.")

    assert isinstance(result, JobAnalysis)
    assert result.mandatory_requirements == ["Python"]
    assert result.preferred_requirements == ["FastAPI"]
    assert result.technical_skills == ["Python", "FastAPI"]
    assert responses.calls[0]["text"]["format"]["name"] == "job_analysis"


def test_explain_match_uses_verified_results() -> None:
    mock = MockLLMProvider()
    resume = mock.analyze_resume("Synthetic Python CV.")
    job = mock.analyze_job("Synthetic Python job.")
    provider, responses = make_provider(FakeResponse(json.dumps(match_payload())))

    result = provider.explain_match(
        resume,
        job,
        verified_results={"score": 0.8, "matched": ["Python"]},
    )

    assert isinstance(result, MatchAnalysis)
    assert result.overall_score == 0.8
    prompt_input = responses.calls[0]["input"]
    assert '"score": 0.8' in prompt_input
    assert "do not override verified results" in prompt_input


def test_malformed_json_response_is_rejected() -> None:
    provider, _ = make_provider(FakeResponse("{not valid json"))

    with pytest.raises(LLMResponseValidationError, match="invalid_json"):
        provider.analyze_resume("Synthetic CV.")


def test_schema_validation_rejects_unexpected_fields_from_openai() -> None:
    payload = resume_payload()
    payload["unexpected"] = "not allowed"
    provider, _ = make_provider(FakeResponse(json.dumps(payload)))

    with pytest.raises(LLMResponseValidationError, match="unexpected"):
        provider.analyze_resume("Synthetic CV.")


def test_empty_response_text_is_rejected() -> None:
    provider, _ = make_provider(FakeResponse(""))

    with pytest.raises(LLMResponseValidationError, match="no JSON text"):
        provider.analyze_resume("Synthetic CV.")


def test_incomplete_response_is_rejected_without_content_leakage() -> None:
    provider, _ = make_provider(
        FakeResponse(
            "",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        )
    )

    with pytest.raises(LLMProviderError, match="incomplete: max_output_tokens"):
        provider.analyze_resume("SENSITIVE_SYNTHETIC_CV")


def test_timeout_is_mapped_to_llm_timeout_error() -> None:
    provider, responses = make_provider(APITimeoutError(request()))

    with pytest.raises(LLMTimeoutError, match="timed out"):
        provider.analyze_resume("Synthetic CV.")

    assert len(responses.calls) == 1


def test_rate_limit_is_mapped_to_llm_rate_limit_error() -> None:
    provider, responses = make_provider(rate_limit_error())

    with pytest.raises(LLMRateLimitError, match="rate limit"):
        provider.analyze_resume("Synthetic CV.")

    assert len(responses.calls) == 1


def test_authentication_error_is_mapped_to_configuration_error() -> None:
    provider, _ = make_provider(authentication_error())

    with pytest.raises(LLMAuthenticationError, match="API key"):
        provider.analyze_resume("Synthetic CV.")


def test_permission_error_is_mapped_to_configuration_error() -> None:
    provider, _ = make_provider(permission_error())

    with pytest.raises(LLMAuthenticationError, match="denied access"):
        provider.analyze_resume("Synthetic CV.")


def test_connection_error_is_mapped_to_provider_unavailable() -> None:
    provider, _ = make_provider(
        APIConnectionError(request=request(), message="Connection error.")
    )

    with pytest.raises(LLMProviderUnavailableError, match="Could not reach OpenAI"):
        provider.analyze_resume("Synthetic CV.")


def test_response_error_field_is_rejected() -> None:
    provider, _ = make_provider(
        FakeResponse(error=SimpleNamespace(message="provider refused"))
    )

    with pytest.raises(LLMProviderError, match="error response"):
        provider.analyze_resume("Synthetic CV.")


def test_dict_shaped_response_is_accepted() -> None:
    provider, _ = make_provider({"output_text": json.dumps(resume_payload())})

    result = provider.analyze_resume("Synthetic CV.")

    assert isinstance(result, ResumeAnalysis)
    assert result.technical_skills == ["Python"]


def test_non_structured_bad_request_is_not_retried_as_json_object() -> None:
    provider, responses = make_provider(
        bad_request("Invalid request: input is too large")
    )

    with pytest.raises(LLMProviderError, match="status 400"):
        provider.analyze_resume("Synthetic CV.")

    assert len(responses.calls) == 1


def test_structured_output_fallback_to_json_object_happens_only_once() -> None:
    provider, responses = make_provider(
        bad_request(),
        FakeResponse(json.dumps(resume_payload())),
    )

    result = provider.analyze_resume("Synthetic CV.")

    assert isinstance(result, ResumeAnalysis)
    assert len(responses.calls) == 2
    assert responses.calls[0]["text"]["format"]["type"] == "json_schema"
    assert responses.calls[1]["text"]["format"]["type"] == "json_object"


def test_free_form_schema_uses_json_object_mode() -> None:
    payload = {
        "schema_name": "invoice",
        "data": {"total": 42},
        "confidence": 0.8,
        "uncertainties": [],
    }
    provider, responses = make_provider(FakeResponse(json.dumps(payload)))

    result = provider.extract_structured("Synthetic invoice total 42.", schema_name="invoice")

    assert isinstance(result, StructuredExtractionResult)
    assert result.data == {"total": 42}
    assert responses.calls[0]["text"]["format"]["type"] == "json_object"


def test_strict_schema_requires_every_property_and_rejects_unknown_keys() -> None:
    schema = build_response_schema(ResumeAnalysis)
    text = json.dumps(schema)

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "maxLength" not in text
    assert "minLength" not in text
    assert "maximum" not in text
    assert "minimum" not in text
    assert response_schema_name(ResumeAnalysis) == "resume_analysis"


def test_free_form_models_are_not_supported_by_strict_schema_builder() -> None:
    with pytest.raises(UnsupportedJSONSchemaError):
        build_response_schema(StructuredExtractionResult)


def test_invalid_verified_results_type_is_rejected() -> None:
    mock = MockLLMProvider()
    resume = mock.analyze_resume("Synthetic CV.")
    job = mock.analyze_job("Synthetic job.")
    provider, _ = make_provider(FakeResponse(json.dumps(match_payload())))

    with pytest.raises(LLMInputError, match="verified_results"):
        provider.explain_match(resume, job, verified_results="not-results")


def test_client_uses_configured_key_timeout_and_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("app.services.llm.openai_provider.OpenAI", FakeOpenAI)
    settings = make_settings(
        OPENAI_TIMEOUT_SECONDS="9.5",
        OPENAI_MAX_RETRIES="3",
    )

    OpenAIProvider(settings)

    assert captured["api_key"] == "sk-test-value"
    assert captured["timeout"] == 9.5
    assert captured["max_retries"] == 3


def test_openai_timeout_and_retry_configuration_is_validated() -> None:
    settings = make_settings(
        OPENAI_TIMEOUT_SECONDS="12.5",
        OPENAI_MAX_RETRIES="1",
    )
    assert settings.openai_timeout_seconds == 12.5
    assert settings.openai_max_retries == 1

    with pytest.raises(LLMConfigurationError, match="OPENAI_TIMEOUT_SECONDS"):
        LLMSettings.from_env(
            {
                "LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "sk-test-value",
                "OPENAI_MODEL": "configured-test-model",
                "OPENAI_TIMEOUT_SECONDS": "not-a-number",
            }
        )

    with pytest.raises(ValidationError):
        LLMSettings(
            provider="openai",
            openai_api_key="sk-test-value",
            openai_model="configured-test-model",
            openai_max_retries=99,
        )


def test_sensitive_content_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="jobmatch.llm")
    provider, _ = make_provider(FakeResponse(json.dumps(resume_payload())))
    sensitive = "SENSITIVE_SYNTHETIC_CV_TEXT"

    provider.analyze_resume(sensitive)

    assert sensitive not in caplog.text
    assert "operation_completed" in caplog.text
    assert "configured-test-model" in caplog.text
    assert "sk-test-value" not in caplog.text
    assert '"prompt_name": "resume_analysis"' in caplog.text
    assert '"prompt_version": "1.1.0"' in caplog.text
