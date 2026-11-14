from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.services.llm.exceptions import LLMConfigurationError

DEFAULT_OPENAI_TIMEOUT_SECONDS = 60.0
DEFAULT_OPENAI_MAX_RETRIES = 2
ProviderName = Literal["openai", "mock"]


def _read_bounded_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise LLMConfigurationError(f"{name} must be a number.") from exc
    if not minimum <= value <= maximum:
        raise LLMConfigurationError(
            f"{name} must be between {minimum:g} and {maximum:g}."
        )
    return value


def _read_bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise LLMConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise LLMConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: str | None = Field(default=None, min_length=1, max_length=120)
    openai_timeout_seconds: float = Field(
        default=DEFAULT_OPENAI_TIMEOUT_SECONDS, gt=0, le=600
    )
    openai_max_retries: int = Field(
        default=DEFAULT_OPENAI_MAX_RETRIES, ge=0, le=5
    )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMSettings:
        env = os.environ if environ is None else environ
        provider = (env.get("LLM_PROVIDER") or "openai").strip().casefold()
        if provider not in {"openai", "mock"}:
            raise LLMConfigurationError(
                "LLM_PROVIDER must be either 'openai' or 'mock'."
            )

        api_key = (env.get("OPENAI_API_KEY") or "").strip()
        model = (env.get("OPENAI_MODEL") or "").strip() or None
        timeout_seconds = _read_bounded_float(
            env,
            "OPENAI_TIMEOUT_SECONDS",
            DEFAULT_OPENAI_TIMEOUT_SECONDS,
            minimum=0.1,
            maximum=600.0,
        )
        max_retries = _read_bounded_int(
            env,
            "OPENAI_MAX_RETRIES",
            DEFAULT_OPENAI_MAX_RETRIES,
            minimum=0,
            maximum=5,
        )
        try:
            settings = cls(
                provider=provider,
                openai_api_key=SecretStr(api_key) if api_key else None,
                openai_model=model,
                openai_timeout_seconds=timeout_seconds,
                openai_max_retries=max_retries,
            )
        except ValidationError as exc:
            raise LLMConfigurationError("Invalid LLM configuration.") from exc

        settings.validate_for_provider()
        return settings

    def validate_for_provider(self) -> None:
        if self.provider == "openai":
            if self.openai_api_key is None:
                raise LLMConfigurationError(
                    "OPENAI_API_KEY is required when LLM_PROVIDER=openai."
                )
            if not self.openai_model:
                raise LLMConfigurationError(
                    "OPENAI_MODEL is required when LLM_PROVIDER=openai."
                )

    @property
    def model_name(self) -> str:
        if self.provider == "openai" and self.openai_model:
            return self.openai_model
        return "mock"

    @property
    def api_key_configured(self) -> bool:
        return self.openai_api_key is not None and bool(
            self.openai_api_key.get_secret_value()
        )

    def public_config(self) -> dict[str, str | bool]:
        return {
            "provider": self.provider,
            "model": self.model_name,
            "api_key_configured": self.api_key_configured,
        }
