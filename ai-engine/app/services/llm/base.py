from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from time import perf_counter
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.logging import get_llm_logger, log_llm_event
from app.schemas.common import AnalysisMetadata, StructuredExtractionResult
from app.schemas.cv_document import CvImprovementOutput
from app.schemas.job_analysis import JobAnalysis
from app.schemas.match_analysis import MatchAnalysis
from app.schemas.resume_analysis import ResumeAnalysis
from app.services.llm import prompt_templates
from app.services.llm.exceptions import (
    LLMError,
    LLMInputError,
    LLMProviderError,
    LLMResponseValidationError,
)
from app.services.llm.prompt_templates import PromptTemplate

ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_INPUT_CHARS = 200_000


def validate_input_text(text: str, field_name: str = "text") -> str:
    if not isinstance(text, str) or not text.strip():
        raise LLMInputError(f"{field_name} must be a non-empty string.")
    if len(text) > MAX_INPUT_CHARS:
        raise LLMInputError(
            f"{field_name} exceeds the maximum supported length of {MAX_INPUT_CHARS} characters."
        )
    return text


def validate_provider_response(
    payload: Any,
    response_model: type[ModelT],
    operation: str,
) -> ModelT:
    if isinstance(payload, response_model):
        return payload
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    elif isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LLMResponseValidationError(
                operation,
                f"invalid_json at line {exc.lineno} column {exc.colno}",
            ) from exc

    if not isinstance(payload, dict):
        raise LLMResponseValidationError(
            operation,
            f"expected a JSON object, received {type(payload).__name__}",
        )

    try:
        return response_model.model_validate(payload)
    except ValidationError as exc:
        raise LLMResponseValidationError.from_validation_error(operation, exc) from exc


class LLMProvider(ABC):
    """Provider-independent interface for structured LLM analysis."""

    provider_name: str = "unknown"
    model_name: str = "unknown"

    @abstractmethod
    def analyze_resume(self, text: str) -> ResumeAnalysis:
        """Analyze résumé text into structured candidate information."""

    @abstractmethod
    def analyze_job(self, text: str) -> JobAnalysis:
        """Analyze a job description into structured requirements."""

    @abstractmethod
    def analyze_job_description(self, text: str) -> JobAnalysis:
        """Analyze a job description into structured requirements."""

    @abstractmethod
    def extract_structured(
        self,
        text: str,
        *,
        schema_name: str = "structured_extraction",
        fields: Sequence[str] | None = None,
        response_model: type[BaseModel] = StructuredExtractionResult,
    ) -> BaseModel:
        """Extract caller-defined structured information from text."""

    @abstractmethod
    def explain_match(
        self,
        resume_analysis: ResumeAnalysis,
        job_analysis: JobAnalysis,
        *,
        verified_results: Any | None = None,
    ) -> MatchAnalysis:
        """Generate a structured explanation of candidate-job fit."""


class BaseLLMProvider(LLMProvider):
    """Template implementation that validates every provider response."""

    def __init__(self) -> None:
        self._logger = get_llm_logger()

    def analyze_resume(self, text: str) -> ResumeAnalysis:
        checked_text = validate_input_text(text, "resume_text")
        prompt = prompt_templates.resume_analysis_prompt(checked_text)
        return self._execute(
            operation="resume_analysis",
            prompt=prompt,
            response_model=ResumeAnalysis,
            input_chars=len(checked_text),
        )

    def analyze_job(self, text: str) -> JobAnalysis:
        checked_text = validate_input_text(text, "job_description")
        prompt = prompt_templates.job_description_analysis_prompt(checked_text)
        return self._execute(
            operation="job_analysis",
            prompt=prompt,
            response_model=JobAnalysis,
            input_chars=len(checked_text),
        )

    def analyze_job_description(self, text: str) -> JobAnalysis:
        return self.analyze_job(text)

    def extract_structured(
        self,
        text: str,
        *,
        schema_name: str = "structured_extraction",
        fields: Sequence[str] | None = None,
        response_model: type[BaseModel] = StructuredExtractionResult,
    ) -> BaseModel:
        checked_text = validate_input_text(text, "extraction_text")
        prompt = prompt_templates.structured_extraction_prompt(
            checked_text,
            schema_name=schema_name,
            fields=fields,
            response_model=response_model,
        )
        return self._execute(
            operation=f"structured_extraction:{response_model.__name__}",
            prompt=prompt,
            response_model=response_model,
            input_chars=len(checked_text),
        )

    def improve_cv(self, items_text: str, *, language: str = "en") -> CvImprovementOutput:
        """Suggest rewrites for CV items. The caller validates every suggestion."""
        checked_text = validate_input_text(items_text, "cv_items")
        prompt = prompt_templates.cv_improvement_prompt(checked_text, language=language)
        return self._execute(
            operation="cv_improvement",
            prompt=prompt,
            response_model=CvImprovementOutput,
            input_chars=len(checked_text),
        )

    def explain_match(
        self,
        resume_analysis: ResumeAnalysis,
        job_analysis: JobAnalysis,
        *,
        verified_results: Any | None = None,
    ) -> MatchAnalysis:
        if not isinstance(resume_analysis, ResumeAnalysis):
            raise LLMInputError("resume_analysis must be a ResumeAnalysis instance.")
        if not isinstance(job_analysis, JobAnalysis):
            raise LLMInputError("job_analysis must be a JobAnalysis instance.")
        if verified_results is not None and not isinstance(
            verified_results, (BaseModel, Mapping, list)
        ):
            raise LLMInputError(
                "verified_results must be a Pydantic model, mapping, list, or None."
            )
        prompt = prompt_templates.match_explanation_prompt(
            resume_analysis,
            job_analysis,
            verified_results=verified_results,
        )
        return self._execute(
            operation="match_explanation",
            prompt=prompt,
            response_model=MatchAnalysis,
            input_chars=len(resume_analysis.model_dump_json()) + len(job_analysis.model_dump_json()),
        )

    def _execute(
        self,
        *,
        operation: str,
        prompt: PromptTemplate,
        response_model: type[ModelT],
        input_chars: int,
    ) -> ModelT:
        started = perf_counter()
        log_llm_event(
            self._logger,
            "operation_started",
            operation=operation,
            provider=self.provider_name,
            model=self.model_name,
            input_chars=input_chars,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )
        try:
            payload = self._complete_json(operation, prompt, response_model)
            result = validate_provider_response(payload, response_model, operation)
            result = self._attach_metadata(result, prompt)
        except LLMError as exc:
            log_llm_event(
                self._logger,
                "operation_failed",
                operation=operation,
                provider=self.provider_name,
                model=self.model_name,
                input_chars=input_chars,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                error_type=type(exc).__name__,
            )
            raise
        except Exception as exc:
            log_llm_event(
                self._logger,
                "operation_failed",
                operation=operation,
                provider=self.provider_name,
                model=self.model_name,
                input_chars=input_chars,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                error_type=type(exc).__name__,
            )
            raise LLMProviderError(
                f"{operation} failed in provider {self.provider_name}."
            ) from exc

        log_llm_event(
            self._logger,
            "operation_completed",
            operation=operation,
            provider=self.provider_name,
            model=self.model_name,
            input_chars=input_chars,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            success=True,
        )
        return result

    def _attach_metadata(self, result: ModelT, prompt: PromptTemplate) -> ModelT:
        if "metadata" not in type(result).model_fields:
            return result
        metadata = AnalysisMetadata(
            provider=self.provider_name,
            model=self.model_name,
            prompt_version=prompt.version,
        )
        return result.model_copy(update={"metadata": metadata})

    @abstractmethod
    def _complete_json(
        self,
        operation: str,
        prompt: PromptTemplate,
        response_model: type[BaseModel],
    ) -> Any:
        """Return a JSON-compatible provider payload for the requested operation."""
