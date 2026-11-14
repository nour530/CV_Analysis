from __future__ import annotations

from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel

from app.config import LLMSettings
from app.core.logging import log_llm_event
from app.services.llm.base import BaseLLMProvider
from app.services.llm.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMProviderError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseValidationError,
    LLMTimeoutError,
)
from app.services.llm.openai_schema import (
    UnsupportedJSONSchemaError,
    build_response_schema,
    response_schema_name,
)
from app.services.llm.prompt_templates import PromptTemplate

MAX_OUTPUT_TOKENS = 8192


class OpenAIProvider(BaseLLMProvider):
    """OpenAI provider backed by the Responses API with structured outputs."""

    provider_name = "openai"
    network_enabled = True

    def __init__(
        self,
        settings: LLMSettings,
        *,
        client: Any | None = None,
    ) -> None:
        super().__init__()
        if settings.provider != "openai":
            raise LLMConfigurationError("OpenAIProvider requires LLM_PROVIDER=openai.")
        if not settings.api_key_configured:
            raise LLMConfigurationError(
                "OPENAI_API_KEY is required before OpenAIProvider can be created."
            )
        if not settings.openai_model:
            raise LLMConfigurationError(
                "OPENAI_MODEL is required before OpenAIProvider can be created."
            )

        self._settings = settings
        self.model_name = settings.openai_model
        self._client = client if client is not None else OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    def build_request_payload(
        self,
        prompt: PromptTemplate,
        response_model: type[BaseModel] | None = None,
        *,
        use_json_schema: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "instructions": prompt.system,
            "input": prompt.user,
            "store": False,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }
        if response_model is not None and use_json_schema:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": response_schema_name(response_model),
                    "schema": build_response_schema(response_model),
                    "strict": True,
                }
            }
        else:
            payload["text"] = {"format": {"type": "json_object"}}
        return payload

    def _complete_json(
        self,
        operation: str,
        prompt: PromptTemplate,
        response_model: type[Any],
    ) -> Any:
        use_json_schema = True
        try:
            payload = self.build_request_payload(prompt, response_model)
        except UnsupportedJSONSchemaError:
            use_json_schema = False
            payload = self.build_request_payload(
                prompt,
                response_model,
                use_json_schema=False,
            )

        try:
            response = self._create_response(
                operation,
                payload,
                allow_structured_output_fallback=use_json_schema,
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError(
                f"OpenAI request timed out during {operation}."
            ) from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(
                f"OpenAI rate limit was exceeded during {operation}."
            ) from exc
        except AuthenticationError as exc:
            raise LLMAuthenticationError(
                "OpenAI rejected the configured API key."
            ) from exc
        except PermissionDeniedError as exc:
            raise LLMAuthenticationError(
                "OpenAI denied access for the configured credential or model."
            ) from exc
        except APIConnectionError as exc:
            raise LLMProviderUnavailableError(
                f"Could not reach OpenAI during {operation}."
            ) from exc
        except BadRequestError as exc:
            raise LLMProviderError(
                f"OpenAI rejected the request for {operation} with status 400."
            ) from exc
        except APIStatusError as exc:
            raise LLMProviderError(
                f"OpenAI request for {operation} failed with status {exc.status_code}."
            ) from exc
        except APIError as exc:
            raise LLMProviderError(
                f"OpenAI request for {operation} failed."
            ) from exc
        except OpenAIError as exc:
            raise LLMProviderError(
                f"OpenAI SDK error during {operation}."
            ) from exc

        return self._extract_response_text(response, operation)

    def _create_response(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        allow_structured_output_fallback: bool,
    ) -> Any:
        try:
            return self._client.responses.create(**payload)
        except BadRequestError as exc:
            if not allow_structured_output_fallback or not self._is_structured_output_error(
                exc
            ):
                raise
            log_llm_event(
                self._logger,
                "structured_output_fallback",
                operation=operation,
                provider=self.provider_name,
                model=self.model_name,
                fallback_format="json_object",
            )
            fallback_payload = dict(payload)
            fallback_payload["text"] = {"format": {"type": "json_object"}}
            return self._client.responses.create(**fallback_payload)

    @staticmethod
    def _is_structured_output_error(error: BadRequestError) -> bool:
        parts = [str(error), str(error.code or ""), str(error.param or "")]
        message = " ".join(parts).casefold()
        return any(
            marker in message
            for marker in (
                "json_schema",
                "structured output",
                "structured outputs",
                "response_format",
                "text.format",
                "additionalproperties",
            )
        )

    @staticmethod
    def _extract_response_text(response: Any, operation: str) -> str:
        text = getattr(response, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text

        if isinstance(response, dict):
            text = response.get("output_text")
            if isinstance(text, str) and text.strip():
                return text

        error = getattr(response, "error", None)
        if error:
            raise LLMProviderError("OpenAI returned an error response.")

        incomplete_details = getattr(response, "incomplete_details", None)
        reason = getattr(incomplete_details, "reason", None)
        if reason:
            raise LLMProviderError(
                f"OpenAI response for {operation} was incomplete: {reason}."
            )

        raise LLMResponseValidationError(
            operation,
            "response contained no JSON text; the model may have refused or returned "
            "an unsupported content block",
        )
