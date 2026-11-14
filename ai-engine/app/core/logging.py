from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from app.core.request_context import get_request_id

LLM_LOGGER_NAME = "jobmatch.llm"
AUDIT_LOGGER_NAME = "jobmatch.audit"
ALERT_LOGGER_NAME = "jobmatch.security.alert"
OPS_LOGGER_NAME = "jobmatch.ops"
_SECURITY_LOGGER_NAMES = (AUDIT_LOGGER_NAME, ALERT_LOGGER_NAME)
_REQUEST_ID_FACTORY_INSTALLED = False

_UNSAFE_KEY_PARTS = (
    "api_key",
    "apikey",
    "candidate",
    "content",
    "cv",
    "email",
    "phone",
    "prompt",
    "raw_response",
    "response",
    "resume",
    "secret",
    "text",
)

# Prompt identifiers are version metadata, not prompt content, so they may be
# logged even though the broader "prompt" key filter rejects prompt bodies.
_SAFE_KEY_ALLOWLIST = frozenset({"prompt_name", "prompt_version"})


def install_request_id_log_attribute() -> None:
    """Attach the active request ID to every LogRecord without replacing handlers."""
    global _REQUEST_ID_FACTORY_INSTALLED
    if _REQUEST_ID_FACTORY_INSTALLED:
        return
    base_factory = logging.getLogRecordFactory()

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base_factory(*args, **kwargs)
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id() or "-"
        return record

    logging.setLogRecordFactory(record_factory)
    _REQUEST_ID_FACTORY_INSTALLED = True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": getattr(record, "request_id", "-") or "-",
        }
        try:
            structured = json.loads(message)
        except (TypeError, ValueError):
            structured = None
        if isinstance(structured, dict):
            payload.update(structured)
            payload.pop("message", None)
            if "event" not in payload and "event_type" not in payload:
                payload["message"] = message
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class RequestIdFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", "-") or "-"
        return (
            f"{self.formatTime(record)} {record.levelname} "
            f"[{request_id}] {record.name}: {record.getMessage()}"
        )


def configure_application_logging(*, level: str = "INFO", json_logs: bool = False) -> None:
    """Configure a safe default root handler without overriding deployments."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter() if json_logs else RequestIdFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def log_operational_event(event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {
        "component": "operations",
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "request_id": get_request_id() or "-",
    }
    for key, value in fields.items():
        if _safe_field(key, value):
            payload[key] = value
    logging.getLogger(OPS_LOGGER_NAME).info(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def configure_security_logging() -> None:
    """Ensure audit/alert records reach the configured logging stack.

    The audit module deliberately uses the standard library logging hierarchy
    instead of a second logging framework. Deployments that already configure
    root handlers keep full control. When no handler is configured (the default
    for a bare ``uvicorn`` process), a single stream handler is attached to the
    security loggers so INFO audit events are not silently discarded.
    """
    root = logging.getLogger()
    if root.handlers:
        # A deployment (or test harness) already configured logging; do not
        # override its levels, handlers, or propagation.
        return
    level_name = (os.getenv("AUDIT_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    for name in _SECURITY_LOGGER_NAMES:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        logger.propagate = True


def get_llm_logger() -> logging.Logger:
    return logging.getLogger(LLM_LOGGER_NAME)


def _safe_field(key: str, value: Any) -> bool:
    lowered = key.casefold()
    if lowered not in _SAFE_KEY_ALLOWLIST and any(
        part in lowered for part in _UNSAFE_KEY_PARTS
    ):
        return False
    return isinstance(value, (str, int, float, bool, type(None)))


def log_llm_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit structured LLM telemetry without prompt or personal content."""
    payload: dict[str, Any] = {"component": "llm", "event": event}
    for key, value in fields.items():
        if _safe_field(key, value):
            payload[key] = value
    logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
