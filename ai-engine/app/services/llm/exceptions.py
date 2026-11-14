from __future__ import annotations

from typing import Any


class LLMError(RuntimeError):
    """Base class for all LLM integration errors."""


class LLMConfigurationError(LLMError):
    """Raised when provider configuration is missing or invalid."""


class LLMInputError(LLMError):
    """Raised when input text is empty or exceeds the supported size."""


class LLMProviderError(LLMError):
    """Raised when a provider cannot complete a request."""


class LLMProviderUnavailableError(LLMProviderError):
    """Raised when a provider is intentionally disabled or unreachable."""


class LLMAuthenticationError(LLMConfigurationError):
    """Raised when the provider rejects the configured credential."""


class LLMRateLimitError(LLMProviderError):
    """Raised when the provider rate limit remains exhausted after retries."""


class LLMTimeoutError(LLMProviderError):
    """Raised when a provider request exceeds the configured timeout."""


class LLMProviderNotFoundError(LLMConfigurationError):
    """Raised when the selected provider is not registered."""


class LLMResponseValidationError(LLMError):
    """Raised when a provider response does not match the expected schema."""

    def __init__(self, operation: str, detail: str) -> None:
        self.operation = operation
        self.detail = detail
        super().__init__(f"Invalid provider response for {operation}: {detail}")

    @classmethod
    def from_validation_error(cls, operation: str, error: Any) -> LLMResponseValidationError:
        errors = getattr(error, "errors", lambda: [])()
        safe_errors: list[str] = []
        for item in errors[:10]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
            error_type = str(item.get("type", "validation_error"))
            message = str(item.get("msg", "Invalid value"))
            safe_errors.append(f"{location}: {error_type}: {message}")
        detail = "; ".join(safe_errors) or "response did not match the expected schema"
        return cls(operation, detail)
