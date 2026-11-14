from __future__ import annotations

import re
from contextvars import ContextVar, Token

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)


def normalize_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate or not REQUEST_ID_PATTERN.fullmatch(candidate):
        return None
    return candidate


def set_request_id(request_id: str) -> Token[str | None]:
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _REQUEST_ID.reset(token)


def get_request_id() -> str | None:
    return _REQUEST_ID.get()
