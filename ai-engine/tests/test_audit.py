from __future__ import annotations

import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.dependencies import get_api_settings
from app.api.settings import APISettings
from app.auth.settings import get_auth_settings
from app.core.audit import (
    ALERT_RULES,
    AlertRule,
    AuditEvent,
    SecurityAlertDispatcher,
    audit_event,
    pseudonymize_identifier,
)
from app.core.request_context import (
    normalize_request_id,
    reset_request_id,
    set_request_id,
)
from app.db.models import User
from app.db.session import get_session_factory
from app.main import app
from conftest import TEST_PASSWORD, register_user


@pytest.fixture
def client_factory():
    clients: list[TestClient] = []

    def _create() -> TestClient:
        settings = APISettings()
        app.dependency_overrides[get_api_settings] = lambda: settings
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield _create

    for client in clients:
        client.__exit__(None, None, None)
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_factory) -> TestClient:
    return client_factory()


def _new_email() -> str:
    return f"audit-{uuid.uuid4().hex}@example.com"


def _audit_events(caplog) -> list[dict]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "jobmatch.audit"
    ]


def _event_types(events: list[dict]) -> list[str]:
    return [event["event_type"] for event in events]


def test_request_id_validation_and_log_correlation(client, caplog):
    assert normalize_request_id("request-123") == "request-123"
    assert normalize_request_id("bad value") is None
    assert normalize_request_id("line\nbreak") is None
    assert normalize_request_id("x" * 121) is None
    assert normalize_request_id("x" * 120) == "x" * 120

    caplog.set_level(logging.INFO, logger="jobmatch.audit")
    response = client.post(
        "/api/v1/auth/register",
        headers={"X-Request-ID": "request-123"},
        json={
            "full_name": "Audit User",
            "email": _new_email(),
            "password": TEST_PASSWORD,
        },
    )
    assert response.status_code == 202
    assert response.headers["x-request-id"] == "request-123"
    events = _audit_events(caplog)
    assert events
    assert {event["request_id"] for event in events} == {"request-123"}

    caplog.clear()
    token = set_request_id("context-request-1")
    try:
        logging.getLogger("jobmatch.audit.test").info("request context check")
    finally:
        reset_request_id(token)
    records = [
        record
        for record in caplog.records
        if record.name == "jobmatch.audit.test"
    ]
    assert records
    assert records[-1].request_id == "context-request-1"


def test_registration_audit_is_structured_and_pseudonymized(client, caplog):
    email = _new_email()
    caplog.set_level(logging.INFO, logger="jobmatch.audit")

    first = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Audit User", "email": email, "password": TEST_PASSWORD},
    )
    second = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Audit User", "email": email, "password": TEST_PASSWORD},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    events = _audit_events(caplog)
    assert _event_types(events) == [
        "auth.registration.requested",
        "auth.registration.succeeded",
        "auth.registration.requested",
        "auth.registration.duplicate",
    ]
    assert events[1]["outcome"] == "success"
    assert events[1]["user_id"]
    assert events[2]["outcome"] == "success"
    assert events[3]["outcome"] == "failure"
    account_ref = pseudonymize_identifier(email)
    assert events[0]["metadata"]["account_ref"] == account_ref
    assert events[3]["metadata"]["account_ref"] == account_ref
    assert email not in caplog.text
    assert TEST_PASSWORD not in caplog.text


def test_login_failure_and_disabled_account_audit(client, caplog):
    email = _new_email()
    register_user(client, email=email, password=TEST_PASSWORD)
    with get_session_factory()() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        user.is_active = False
        db.commit()
        user_id = user.id

    caplog.set_level(logging.INFO, logger="jobmatch.audit")
    disabled = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    unknown_email = _new_email()
    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": unknown_email, "password": TEST_PASSWORD},
    )

    assert disabled.status_code == 401
    assert disabled.json()["error"]["code"] == "invalid_credentials"
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "invalid_credentials"
    events = _audit_events(caplog)
    assert _event_types(events) == ["auth.login.disabled", "auth.login.failed"]
    assert events[0]["user_id"] == user_id
    assert events[0]["metadata"]["reason"] == "account_disabled"
    assert events[1]["outcome"] == "failure"
    assert events[1]["metadata"]["reason"] == "invalid_credentials"
    assert "user_id" not in events[1]
    assert email not in caplog.text
    assert unknown_email not in caplog.text
    assert TEST_PASSWORD not in caplog.text


def test_refresh_success_and_reuse_audit(client, client_factory, caplog):
    settings = get_auth_settings()
    email = _new_email()
    login = register_user(client, email=email, password=TEST_PASSWORD)
    old_refresh_token = client.cookies.get(settings.refresh_cookie_name)
    assert old_refresh_token

    caplog.set_level(logging.INFO, logger="jobmatch.audit")
    refreshed = client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": login["csrf_token"]},
    )
    assert refreshed.status_code == 200
    refreshed_payload = refreshed.json()

    reuse_client = client_factory()
    reuse_client.cookies.set(
        settings.refresh_cookie_name,
        old_refresh_token,
        path=settings.cookie_path,
    )
    reuse_client.cookies.set(
        settings.csrf_cookie_name,
        refreshed_payload["csrf_token"],
        path=settings.csrf_cookie_path,
    )
    reused = reuse_client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": refreshed_payload["csrf_token"]},
    )
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "refresh_token_reused"

    events = _audit_events(caplog)
    assert _event_types(events) == [
        "auth.refresh.succeeded",
        "auth.refresh.reuse_detected",
        "auth.session.revoked",
    ]
    assert events[0]["outcome"] == "success"
    assert events[1]["outcome"] == "failure"
    assert events[2]["metadata"]["reason"] == "refresh_token_reused"
    assert old_refresh_token not in caplog.text
    assert refreshed_payload["csrf_token"] not in caplog.text


def test_logout_failure_and_success_audit(client, caplog):
    email = _new_email()
    login = register_user(client, email=email, password=TEST_PASSWORD)
    settings = get_auth_settings()
    refresh_token = client.cookies.get(settings.refresh_cookie_name)
    assert refresh_token

    caplog.set_level(logging.INFO, logger="jobmatch.audit")
    client.headers.pop("X-CSRF-Token")
    failed = client.post("/api/v1/auth/logout")
    assert failed.status_code == 403
    assert failed.json()["error"]["code"] == "csrf_failed"

    client.headers["X-CSRF-Token"] = login["csrf_token"]
    succeeded = client.post("/api/v1/auth/logout")
    assert succeeded.status_code == 204

    events = _audit_events(caplog)
    assert _event_types(events) == [
        "auth.csrf.failed",
        "auth.logout.failed",
        "auth.logout.succeeded",
        "auth.session.revoked",
    ]
    assert events[0]["metadata"]["action"] == "logout"
    assert events[1]["metadata"]["reason"] == "csrf_failed"
    assert events[2]["outcome"] == "success"
    assert events[3]["metadata"]["reason"] == "logout"
    assert refresh_token not in caplog.text
    assert login["csrf_token"] not in caplog.text
    assert TEST_PASSWORD not in caplog.text


def test_password_change_audit_and_mass_revocation(client, client_factory, caplog):
    email = _new_email()
    register_user(client, email=email, password=TEST_PASSWORD)
    second_client = client_factory()
    second_login = second_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert second_login.status_code == 200
    new_password = "New-Correct-Horse-9-Battery"

    caplog.set_level(logging.INFO, logger="jobmatch.audit")
    failed = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "wrong-password",
            "new_password": new_password,
        },
    )
    assert failed.status_code == 401
    assert failed.json()["error"]["code"] == "invalid_current_password"

    succeeded = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": new_password,
        },
    )
    assert succeeded.status_code == 204

    events = _audit_events(caplog)
    assert _event_types(events) == [
        "auth.password_change.failed",
        "auth.password_change.succeeded",
        "auth.session.mass_revoked",
    ]
    assert events[0]["metadata"]["reason"] == "invalid_current_password"
    assert events[1]["outcome"] == "success"
    assert events[2]["metadata"]["revoked_sessions"] == 1
    assert TEST_PASSWORD not in caplog.text
    assert new_password not in caplog.text
    assert "wrong-password" not in caplog.text


def test_audit_redacts_sensitive_values(caplog):
    caplog.set_level(logging.INFO, logger="jobmatch.audit")
    email = "alice@example.com"
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "signaturepart123456"
    )
    long_token = "A1b2C3d4" * 10
    audit_event(
        "auth.sensitive.regression",
        "failure",
        metadata={
            "password": "correct-horse-battery-staple",
            "access_token": "access-token-value",
            "refresh_token": "refresh-token-value",
            "jwt_secret": "jwt-secret-value",
            "csrf_token": "csrf-token-value",
            "resume_text": "Jane Doe full resume contents",
            "prompt": "full LLM prompt contents",
            "response": "full LLM response contents",
            "email": email,
            "note": f"contact {email}",
            "jwt": jwt,
            "opaque": long_token,
        },
    )

    events = _audit_events(caplog)
    assert len(events) == 1
    metadata = events[0]["metadata"]
    for key in (
        "password",
        "access_token",
        "refresh_token",
        "jwt_secret",
        "csrf_token",
        "resume_text",
        "prompt",
        "response",
        "email",
    ):
        assert metadata[key] == "[REDACTED]"
    assert metadata["note"] == "contact [REDACTED_EMAIL]"
    assert metadata["jwt"] == "[REDACTED]"
    assert metadata["opaque"] == "[REDACTED_TOKEN]"
    for secret in (
        "correct-horse-battery-staple",
        "access-token-value",
        "refresh-token-value",
        "jwt-secret-value",
        "csrf-token-value",
        "Jane Doe full resume contents",
        "full LLM prompt contents",
        "full LLM response contents",
        email,
        jwt,
        long_token,
    ):
        assert secret not in caplog.text


def test_audit_logging_failure_does_not_break_authentication(client, monkeypatch):
    email = _new_email()
    register_user(client, email=email, password=TEST_PASSWORD)

    class BrokenLogger:
        def info(self, *_args, **_kwargs) -> None:
            raise RuntimeError("audit sink unavailable")

    monkeypatch.setattr("app.core.audit.SECURITY_AUDIT._logger", BrokenLogger())
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_audit_record_failure_does_not_break_authentication(client, monkeypatch):
    email = _new_email()
    register_user(client, email=email, password=TEST_PASSWORD)

    def broken_record(_event) -> None:
        raise RuntimeError("audit subsystem unavailable")

    monkeypatch.setattr("app.core.audit.SECURITY_AUDIT.record", broken_record)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_security_logging_configuration_is_non_destructive():
    import logging

    from app.core.logging import configure_security_logging

    root = logging.getLogger()
    audit_logger = logging.getLogger("jobmatch.audit")
    alert_logger = logging.getLogger("jobmatch.security.alert")
    original_root_handlers = list(root.handlers)
    original_audit_handlers = list(audit_logger.handlers)
    original_alert_handlers = list(alert_logger.handlers)
    try:
        configure_security_logging()
        configure_security_logging()
        if original_root_handlers or original_audit_handlers:
            assert audit_logger.handlers == original_audit_handlers
        else:
            assert len(audit_logger.handlers) <= 1
        if original_root_handlers or original_alert_handlers:
            assert alert_logger.handlers == original_alert_handlers
        else:
            assert len(alert_logger.handlers) <= 1
        assert audit_logger.propagate is True
        assert alert_logger.propagate is True
    finally:
        for logger, handlers in (
            (audit_logger, original_audit_handlers),
            (alert_logger, original_alert_handlers),
        ):
            for handler in list(logger.handlers):
                if handler not in handlers:
                    logger.removeHandler(handler)


def test_alert_dispatcher_thresholds_cooldown_and_isolation():
    assert set(ALERT_RULES) == {
        "login_failures",
        "refresh_token_reuse",
        "csrf_failures",
        "rate_limit_rejections",
        "mass_session_revocation",
    }
    now = [0.0]
    alerts: list[dict] = []
    dispatcher = SecurityAlertDispatcher(
        rules={
            "login_failures": AlertRule(
                alert_type="repeated_login_failures",
                threshold=2,
                window_seconds=10,
                cooldown_seconds=30,
            )
        },
        clock=lambda: now[0],
        sinks=[alerts.append],
    )
    event = AuditEvent(
        "auth.login.failed",
        "failure",
        request_id="request-1",
        client_ip="127.0.0.1",
        metadata={"password": "must-not-leak"},
    )

    dispatcher.observe(event)
    assert alerts == []
    now[0] = 1.0
    dispatcher.observe(event)
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "repeated_login_failures"
    assert alerts[0]["event_count"] == 2
    assert "must-not-leak" not in json.dumps(alerts[0])

    now[0] = 2.0
    dispatcher.observe(event)
    dispatcher.observe(event)
    assert len(alerts) == 1

    now[0] = 40.0
    dispatcher.observe(event)
    now[0] = 41.0
    dispatcher.observe(event)
    assert len(alerts) == 2

    broken_dispatcher = SecurityAlertDispatcher(
        clock=lambda: 0.0,
        sinks=[lambda _payload: (_ for _ in ()).throw(RuntimeError("sink failed"))],
    )
    broken_dispatcher.observe(AuditEvent("auth.refresh.reuse_detected", "failure"))


def test_alert_dispatcher_covers_security_event_categories():
    categories: list[str] = []
    rules = {
        category: AlertRule(
            alert_type=category,
            threshold=1,
            window_seconds=10,
            cooldown_seconds=10,
        )
        for category in ALERT_RULES
    }
    dispatcher = SecurityAlertDispatcher(
        rules=rules,
        clock=lambda: 0.0,
        sinks=[lambda payload: categories.append(payload["category"])],
    )
    for event_type in (
        "auth.login.failed",
        "auth.refresh.reuse_detected",
        "auth.csrf.failed",
        "auth.rate_limit.rejected",
        "auth.session.mass_revoked",
    ):
        dispatcher.observe(AuditEvent(event_type, "failure"))

    assert categories == [
        "login_failures",
        "refresh_token_reuse",
        "csrf_failures",
        "rate_limit_rejections",
        "mass_session_revocation",
    ]


def test_rate_limit_rejections_are_audited(client, caplog):
    settings = APISettings(
        login_rate_limit_attempts=1,
        login_rate_limit_window_seconds=300,
    )
    app.dependency_overrides[get_api_settings] = lambda: settings
    email = _new_email()
    caplog.set_level(logging.INFO, logger="jobmatch.audit")

    first = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    second = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    events = _audit_events(caplog)
    assert _event_types(events) == [
        "auth.login.failed",
        "auth.rate_limit.rejected",
    ]
    assert events[1]["metadata"]["scope"] == "login-account"
    assert TEST_PASSWORD not in caplog.text
