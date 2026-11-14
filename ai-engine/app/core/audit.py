from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.core.request_context import get_request_id

AUDIT_LOGGER_NAME = "jobmatch.audit"
ALERT_LOGGER_NAME = "jobmatch.security.alert"

_OUTCOME_SUCCESS = "success"
_OUTCOME_FAILURE = "failure"

_MAX_METADATA_KEYS = 50
_MAX_METADATA_DEPTH = 4
_MAX_STRING_LENGTH = 256

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "jwt",
    "passwd",
    "password",
    "private_key",
    "prompt",
    "raw_response",
    "refresh_token",
    "response",
    "resume",
    "secret",
    "session_id",
    "session_key",
    "session_token",
    "token",
    "cv_content",
    "cv_text",
    "email",
    "phone",
    "ssn",
)

_EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_BEARER_PATTERN = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b",
    re.IGNORECASE,
)
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact_string(value: str) -> str:
    redacted = _BEARER_PATTERN.sub("[REDACTED_TOKEN]", value)
    redacted = _JWT_PATTERN.sub("[REDACTED_TOKEN]", redacted)
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", redacted)
    redacted = _LONG_TOKEN_PATTERN.sub("[REDACTED_TOKEN]", redacted)
    if len(redacted) > _MAX_STRING_LENGTH:
        return f"{redacted[:_MAX_STRING_LENGTH]}...[truncated]"
    return redacted


def _sanitize_identifier(value: str) -> str:
    redacted = "".join(character for character in value if character.isprintable())
    if len(redacted) > 120:
        return f"{redacted[:120]}...[truncated]"
    return redacted


_IDENTIFIER_KEYS = frozenset(
    {"request_id", "last_request_id", "user_id", "last_user_id", "client_ip"}
)


def _sanitize_value(value: Any, depth: int = 0, *, key: str = "") -> Any:
    if depth > _MAX_METADATA_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, str):
        if key.casefold() in _IDENTIFIER_KEYS:
            return _sanitize_identifier(value)
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_METADATA_KEYS:
                sanitized["_truncated"] = True
                break
            key_text = str(key)[:128]
            sanitized[key_text] = (
                "[REDACTED]"
                if _is_sensitive_key(key_text)
                else _sanitize_value(item, depth + 1, key=key_text)
            )
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        sanitized_items = [
            _sanitize_value(item, depth + 1) for item in items[:_MAX_METADATA_KEYS]
        ]
        if len(items) > _MAX_METADATA_KEYS:
            sanitized_items.append("[TRUNCATED]")
        return sanitized_items
    return "[UNSUPPORTED]"


def sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    sanitized = _sanitize_value(metadata, depth=0)
    return sanitized if isinstance(sanitized, dict) else {}


def pseudonymize_identifier(value: str) -> str:
    normalized = str(value).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    outcome: str
    request_id: str | None = None
    user_id: str | None = None
    client_ip: str | None = None
    metadata: Mapping[str, Any] | None = None
    timestamp: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in {_OUTCOME_SUCCESS, _OUTCOME_FAILURE}:
            raise ValueError("audit outcome must be 'success' or 'failure'")
        if not self.timestamp:
            object.__setattr__(
                self,
                "timestamp",
                datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "component": "security_audit",
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "outcome": self.outcome,
            "request_id": (
                _sanitize_identifier(self.request_id)
                if self.request_id is not None
                else None
            ),
        }
        if self.user_id is not None:
            payload["user_id"] = _sanitize_identifier(self.user_id)
        if self.client_ip is not None:
            payload["client_ip"] = _sanitize_identifier(self.client_ip)
        metadata = sanitize_metadata(self.metadata)
        if metadata:
            payload["metadata"] = metadata
        return payload


@dataclass(frozen=True)
class AlertRule:
    alert_type: str
    threshold: int
    window_seconds: float
    cooldown_seconds: float


ALERT_RULES: dict[str, AlertRule] = {
    "login_failures": AlertRule(
        alert_type="repeated_login_failures",
        threshold=5,
        window_seconds=300,
        cooldown_seconds=300,
    ),
    "refresh_token_reuse": AlertRule(
        alert_type="refresh_token_reuse",
        threshold=1,
        window_seconds=300,
        cooldown_seconds=300,
    ),
    "csrf_failures": AlertRule(
        alert_type="repeated_csrf_failures",
        threshold=5,
        window_seconds=300,
        cooldown_seconds=300,
    ),
    "rate_limit_rejections": AlertRule(
        alert_type="excessive_rate_limit_rejections",
        threshold=10,
        window_seconds=60,
        cooldown_seconds=120,
    ),
    "mass_session_revocation": AlertRule(
        alert_type="mass_session_revocation",
        threshold=1,
        window_seconds=300,
        cooldown_seconds=300,
    ),
}

_ALERT_CATEGORY_BY_EVENT = {
    "auth.login.failed": "login_failures",
    "auth.login.disabled": "login_failures",
    "auth.refresh.reuse_detected": "refresh_token_reuse",
    "auth.csrf.failed": "csrf_failures",
    "auth.rate_limit.rejected": "rate_limit_rejections",
    "auth.session.mass_revoked": "mass_session_revocation",
}


def _log_security_alert(payload: Mapping[str, Any]) -> None:
    logging.getLogger(ALERT_LOGGER_NAME).warning(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


class SecurityAlertDispatcher:
    def __init__(
        self,
        rules: Mapping[str, AlertRule] | None = None,
        *,
        clock: Callable[[], float] | None = None,
        sinks: list[Callable[[Mapping[str, Any]], None]] | None = None,
    ) -> None:
        self._rules = dict(rules or ALERT_RULES)
        self._clock = clock or time.monotonic
        self._sinks = list(sinks) if sinks is not None else [_log_security_alert]
        self._events: dict[str, deque[float]] = {}
        self._last_alert_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def register_sink(self, sink: Callable[[Mapping[str, Any]], None]) -> None:
        with self._lock:
            self._sinks.append(sink)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_alert_at.clear()

    def observe(self, event: AuditEvent, *, now: float | None = None) -> None:
        category = _ALERT_CATEGORY_BY_EVENT.get(event.event_type)
        if category is None:
            return
        rule = self._rules.get(category)
        if rule is None:
            return
        current_time = self._clock() if now is None else now
        payload: dict[str, Any] | None = None
        sinks: list[Callable[[Mapping[str, Any]], None]] = []
        with self._lock:
            bucket = self._events.setdefault(category, deque())
            cutoff = current_time - rule.window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            bucket.append(current_time)
            if len(bucket) < rule.threshold:
                return
            last_alert_at = self._last_alert_at.get(category)
            if (
                last_alert_at is not None
                and current_time - last_alert_at < rule.cooldown_seconds
            ):
                return
            event_count = len(bucket)
            bucket.clear()
            self._last_alert_at[category] = current_time
            payload = {
                "component": "security_alert",
                "alert_type": rule.alert_type,
                "category": category,
                "event_count": event_count,
                "window_seconds": rule.window_seconds,
                "cooldown_seconds": rule.cooldown_seconds,
                "last_event_type": event.event_type,
                "last_request_id": event.request_id,
                "last_user_id": event.user_id,
                "client_ip": event.client_ip,
            }
            payload = sanitize_metadata(payload)
            sinks = list(self._sinks)

        for sink in sinks:
            try:
                sink(payload)
            except Exception:
                continue


class SecurityAuditLogger:
    def __init__(
        self,
        logger: logging.Logger | None = None,
        dispatcher: SecurityAlertDispatcher | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(AUDIT_LOGGER_NAME)
        self._dispatcher = dispatcher or SecurityAlertDispatcher()

    @property
    def dispatcher(self) -> SecurityAlertDispatcher:
        return self._dispatcher

    def record(self, event: AuditEvent) -> None:
        try:
            payload = event.to_payload()
            self._logger.info(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except Exception:
            pass
        try:
            self._dispatcher.observe(event)
        except Exception:
            pass


SECURITY_AUDIT = SecurityAuditLogger()


def audit_event(
    event_type: str,
    outcome: str,
    *,
    request: Any | None = None,
    request_id: str | None = None,
    user_id: str | None = None,
    client_ip: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    try:
        resolved_request_id = request_id
        if resolved_request_id is None and request is not None:
            resolved_request_id = getattr(request.state, "request_id", None)
        if resolved_request_id is None:
            resolved_request_id = get_request_id()
        if client_ip is None and request is not None:
            client = getattr(request, "client", None)
            client_ip = getattr(client, "host", None)
        SECURITY_AUDIT.record(
            AuditEvent(
                event_type=event_type,
                outcome=outcome,
                request_id=resolved_request_id,
                user_id=user_id,
                client_ip=client_ip,
                metadata=metadata,
            )
        )
    except Exception:
        # Audit logging is best-effort and must never break authentication.
        return


def reset_alert_state() -> None:
    SECURITY_AUDIT.dispatcher.reset()
