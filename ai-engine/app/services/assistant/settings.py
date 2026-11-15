from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    try:
        value = int((env.get(name) or "").strip() or default)
    except ValueError:
        return default
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class AssistantSettings:
    """Career assistant configuration (Anthropic Claude).

    ASSISTANT_ENABLED   turn the assistant on (default false)
    ANTHROPIC_API_KEY   read by the Anthropic SDK; never stored by the app
    ASSISTANT_MODEL     default claude-opus-5-5
    ASSISTANT_EFFORT    low | medium | high | xhigh | max (default low: chat)
    """

    enabled: bool = False
    model: str = "claude-opus-5-5"
    effort: str = "low"
    max_message_chars: int = 2000
    history_messages: int = 20
    max_tool_rounds: int = 4
    timeout_seconds: int = 90
    quota_attempts: int = 60
    quota_window_seconds: int = 3600

    @property
    def has_credentials(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    @property
    def ready(self) -> bool:
        return self.enabled and self.has_credentials

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AssistantSettings:
        env = os.environ if environ is None else environ
        effort = (env.get("ASSISTANT_EFFORT") or "low").strip().casefold()
        return cls(
            enabled=(env.get("ASSISTANT_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"},
            model=(env.get("ASSISTANT_MODEL") or "claude-opus-5-5").strip(),
            effort=effort if effort in EFFORTS else "low",
            max_message_chars=_int(env, "ASSISTANT_MAX_MESSAGE_CHARS", 2000, 100, 8000),
            history_messages=_int(env, "ASSISTANT_HISTORY_MESSAGES", 20, 2, 60),
            max_tool_rounds=_int(env, "ASSISTANT_MAX_TOOL_ROUNDS", 4, 0, 8),
            timeout_seconds=_int(env, "ASSISTANT_TIMEOUT_SECONDS", 90, 10, 600),
            quota_attempts=_int(env, "USER_QUOTA_ASSISTANT_ATTEMPTS", 60, 0, 10_000),
            quota_window_seconds=_int(env, "USER_QUOTA_ASSISTANT_WINDOW_SECONDS", 3600, 60, 86_400),
        )
