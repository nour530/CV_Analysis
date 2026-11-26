"""Free local AI through Ollama (https://ollama.com), an alternative to Claude.

Set AI_PROVIDER=ollama to run the career assistant and the AI CV rewrite on a
model served by Ollama on this machine: free, no account, and nothing leaves
the server. Settings:

    AI_PROVIDER=ollama              # default: anthropic
    OLLAMA_URL=http://127.0.0.1:11434
    OLLAMA_MODEL=qwen2.5-coder:7b
    OLLAMA_TIMEOUT_SECONDS=300

Small local models are slower and less capable than Claude; the app's own
checks (tool results, fact warnings on rewrites) stay in place.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5-coder:7b"


class LocalLLMError(RuntimeError):
    """Ollama is not running, the model is missing, or it answered badly."""


@dataclass(frozen=True, slots=True)
class LocalLLMSettings:
    url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 300

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LocalLLMSettings:
        env = os.environ if env is None else env
        try:
            timeout = max(30, min(1800, int(env.get("OLLAMA_TIMEOUT_SECONDS") or 300)))
        except ValueError:
            timeout = 300
        return cls(
            url=(env.get("OLLAMA_URL") or DEFAULT_URL).rstrip("/"),
            model=(env.get("OLLAMA_MODEL") or DEFAULT_MODEL).strip(),
            timeout_seconds=timeout,
        )


def provider(env: Mapping[str, str] | None = None) -> str:
    """'ollama' or 'anthropic' (default)."""
    env = os.environ if env is None else env
    return "ollama" if (env.get("AI_PROVIDER") or "").strip().casefold() == "ollama" else "anthropic"


def ollama_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool definitions -> Ollama (OpenAI-style) function tools."""
    return [
        {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}}
        for t in tools
    ]


def chat(
    settings: LocalLLMSettings,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    schema: dict[str, Any] | None = None,
    temperature: float = 0.3,
    client: Any | None = None,
) -> dict[str, Any]:
    """One /api/chat call; returns Ollama's `message` ({role, content, tool_calls?})."""
    payload: dict[str, Any] = {
        "model": settings.model,
        "stream": False,
        "messages": messages,
        "options": {"temperature": temperature, "num_ctx": 8192},
    }
    if tools:
        payload["tools"] = tools
    if schema:
        payload["format"] = schema
    http = client or httpx
    try:
        response = http.post(f"{settings.url}/api/chat", json=payload, timeout=float(settings.timeout_seconds))
    except httpx.TimeoutException as exc:
        raise LocalLLMError("The local AI took too long to answer.") from exc
    except httpx.HTTPError as exc:
        raise LocalLLMError("The local AI (Ollama) is not running on this server.") from exc
    if response.status_code == 404:
        raise LocalLLMError(f"The local AI model '{settings.model}' is not installed (run: ollama pull {settings.model}).")
    if response.status_code == 400 and tools and "does not support tools" in response.text:
        # Some models cannot call tools: answer without them.
        return chat(settings, messages, schema=schema, temperature=temperature, client=client)
    if response.status_code >= 400:
        raise LocalLLMError("The local AI returned an error.")
    message = response.json().get("message")
    if not isinstance(message, dict):
        raise LocalLLMError("The local AI returned an unreadable answer.")
    return message
