from __future__ import annotations

import csv
import itertools
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_api_settings
from app.api.settings import APIConfigurationError, APISettings
from app.auth.common_passwords import is_common_password
from app.auth.passwords import password_format_errors, password_strength_errors
from app.core.rate_limit import SlidingWindowRateLimiter
from app.main import app
from app.services.llm.mock_provider import MockLLMProvider
import app.api.routes.matching as matching_routes
from conftest import TEST_PASSWORD, register_user

FIELDNAMES = [
    "job_id",
    "label_clean",
    "clean_description",
    "job_title",
    "required_skills",
    "preferred_skills",
    "other_skills",
    "all_extracted_skills",
    "responsibilities",
    "qualifications",
]

ROWS = [
    {
        "job_id": "JOB_1",
        "label_clean": "Software Development",
        "clean_description": "Build backend services with Python, Docker, and React.",
        "job_title": "Backend Engineer",
        "required_skills": "Python",
        "preferred_skills": "Docker",
        "other_skills": "React",
        "all_extracted_skills": "Python;Docker;React",
        "responsibilities": "Build APIs | Maintain services",
        "qualifications": "3+ years experience | Strong communication",
    }
]


def _new_email() -> str:
    return f"abuse-{uuid.uuid4().hex}@example.com"


def _audit_events(caplog) -> list[dict]:
    import json

    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "jobmatch.audit"
    ]


def _event_types(events: list[dict]) -> list[str]:
    return [event["event_type"] for event in events]


@pytest.fixture
def catalog_path(tmp_path):
    path = tmp_path / "job_features.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(ROWS)
    return path


@pytest.fixture
def client_factory(catalog_path):
    clients: list[TestClient] = []

    def _create(**overrides) -> TestClient:
        options = {
            "jobs_features_path": catalog_path,
            "default_recommendation_limit": 3,
            "max_recommendation_limit": 10,
            "default_candidate_pool_size": 3,
            "max_candidate_pool_size": 10,
        }
        options.update(overrides)
        settings = APISettings(**options)
        app.dependency_overrides[get_api_settings] = lambda: settings
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield _create

    for client in clients:
        client.__exit__(None, None, None)
    app.dependency_overrides.clear()


def test_login_account_limit_is_independent_of_client_ip(
    client_factory, monkeypatch
):
    client = client_factory(
        login_rate_limit_attempts=2,
        login_rate_limit_window_seconds=300,
    )
    email = _new_email()
    register_user(client, email=email)
    client.headers.pop("Authorization")
    client.headers.pop("X-CSRF-Token", None)

    addresses = itertools.cycle(
        ["203.0.113.10", "203.0.113.11", "203.0.113.12"]
    )
    monkeypatch.setattr(
        "app.core.rate_limit.client_ip",
        lambda *args, **kwargs: next(addresses),
    )

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Wrong-Password-9"},
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Wrong-Password-9"},
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert float(limited.headers["retry-after"]) >= 1


def test_successful_login_resets_account_failure_budget(client_factory):
    client = client_factory(
        login_rate_limit_attempts=2,
        login_rate_limit_window_seconds=300,
    )
    email = _new_email()
    register_user(client, email=email)
    client.headers.pop("Authorization")
    client.headers.pop("X-CSRF-Token", None)

    wrong = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Wrong-Password-9"},
    )
    assert wrong.status_code == 401

    success = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert success.status_code == 200

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Wrong-Password-9"},
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Wrong-Password-9"},
    )
    assert limited.status_code == 429


def test_common_password_registration_is_rejected_and_audited(
    client_factory, caplog
):
    client = client_factory()
    caplog.set_level(logging.INFO, logger="jobmatch.audit")
    password = "Password123!"

    response = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Common Password",
            "email": _new_email(),
            "password": password,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "password_too_common"
    assert password not in response.text
    events = _audit_events(caplog)
    assert "auth.password.common_rejected" in _event_types(events)
    common_event = next(
        event
        for event in events
        if event["event_type"] == "auth.password.common_rejected"
    )
    assert common_event["metadata"]["action"] == "registration"
    assert password not in caplog.text


def test_common_password_change_is_rejected_and_audited(
    client_factory, caplog
):
    client = client_factory()
    email = _new_email()
    register_user(client, email=email)
    caplog.set_level(logging.INFO, logger="jobmatch.audit")
    password = "Password123!"

    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": password,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "password_too_common"
    assert password not in response.text
    events = _audit_events(caplog)
    common_event = next(
        event
        for event in events
        if event["event_type"] == "auth.password.common_rejected"
    )
    assert common_event["metadata"]["action"] == "password_change"
    assert password not in caplog.text

    client.headers.pop("Authorization")
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert login.status_code == 200


def test_password_policy_helpers_include_common_password_screening():
    password = "Password123!"

    assert password_format_errors(password) == []
    assert "Choose a less common password." in password_strength_errors(password)
    assert is_common_password("P@ssw0rd123!")


def test_common_password_check_can_be_disabled(client_factory):
    client = client_factory(common_password_check_enabled=False)
    email = _new_email()
    password = "Password123!"

    registration = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Disabled Check",
            "email": email,
            "password": password,
        },
    )
    assert registration.status_code == 202

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200


def test_cv_analysis_quota_is_per_user_and_audited(client_factory, caplog):
    client = client_factory(
        user_quota_cv_analysis_attempts=1,
        user_quota_cv_analysis_window_seconds=300,
    )
    register_user(client)
    caplog.set_level(logging.INFO, logger="jobmatch.audit")

    payload = {"resume_text": "Python developer using Docker and React."}
    first = client.post("/api/v1/cv/analyze", json=payload)
    second = client.post("/api/v1/cv/analyze", json=payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "usage_quota_exceeded"
    assert float(second.headers["retry-after"]) >= 1
    events = _audit_events(caplog)
    quota_event = next(
        event
        for event in events
        if event["event_type"] == "security.quota.rejected"
    )
    assert quota_event["metadata"]["operation"] == "cv_analysis"


def test_upload_over_quota_is_rejected_before_the_file_is_parsed(client_factory, monkeypatch):
    import app.api.uploads as uploads

    client = client_factory(
        user_quota_cv_analysis_attempts=1,
        user_quota_cv_analysis_window_seconds=300,
    )
    register_user(client)
    parsed = []
    original = uploads._EXTRACTOR.extract

    def counting_extract(*args, **kwargs):
        parsed.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(uploads._EXTRACTOR, "extract", counting_extract)
    cv = ("resume.txt", b"Python developer using Docker and React.", "text/plain")

    first = client.post("/api/v1/cv/analyze/upload", files={"file": cv})
    second = client.post("/api/v1/cv/analyze/upload", files={"file": cv})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "usage_quota_exceeded"
    assert len(parsed) == 1, "the over-quota upload must not be parsed"


def test_upload_parsing_and_analysis_run_off_the_event_loop(client_factory, monkeypatch):
    import threading

    import app.api.routes.cv as cv_routes
    import app.api.uploads as uploads

    client = client_factory()
    register_user(client)
    threads: dict[str, str] = {}
    original_extract = uploads._EXTRACTOR.extract
    original_save = cv_routes.save_cv_analysis

    def recording_extract(*args, **kwargs):
        threads["extract"] = threading.current_thread().name
        return original_extract(*args, **kwargs)

    def recording_save(*args, **kwargs):
        threads["save"] = threading.current_thread().name
        return original_save(*args, **kwargs)

    monkeypatch.setattr(uploads._EXTRACTOR, "extract", recording_extract)
    monkeypatch.setattr(cv_routes, "save_cv_analysis", recording_save)
    response = client.post(
        "/api/v1/cv/analyze/upload",
        files={"file": ("resume.txt", b"Python developer using Docker.", "text/plain")},
    )

    assert response.status_code == 200
    # Starlette's thread pool (AnyIO) names its threads "AnyIO worker thread";
    # the event loop runs elsewhere, so blocking work there would show another name.
    assert "worker" in threads["extract"].lower(), threads
    assert "worker" in threads["save"].lower(), threads


def test_validation_failure_does_not_consume_cv_quota(client_factory):
    client = client_factory(
        max_text_length=50,
        user_quota_cv_analysis_attempts=1,
        user_quota_cv_analysis_window_seconds=300,
    )
    register_user(client)

    rejected = client.post(
        "/api/v1/cv/analyze",
        json={"resume_text": "x" * 60},
    )
    accepted = client.post(
        "/api/v1/cv/analyze",
        json={"resume_text": "Python developer using Docker."},
    )

    assert rejected.status_code == 413
    assert accepted.status_code == 200


def test_invalid_recommendation_request_does_not_consume_quota(client_factory):
    client = client_factory(
        user_quota_recommendations_attempts=1,
        user_quota_recommendations_window_seconds=300,
    )
    register_user(client)

    rejected = client.post(
        "/api/v1/matches/recommendations",
        json={"resume_text": "Python developer.", "limit": 11},
    )
    accepted = client.post(
        "/api/v1/matches/recommendations",
        json={"resume_text": "Python developer using Docker."},
    )

    assert rejected.status_code == 422
    assert accepted.status_code == 200


def test_job_match_quota_and_repeated_matching_audit(client_factory, caplog):
    client = client_factory(
        user_quota_job_match_attempts=1,
        user_quota_job_match_window_seconds=300,
    )
    register_user(client)
    caplog.set_level(logging.INFO, logger="jobmatch.audit")

    payload = {
        "resume_text": "Python engineer using Docker and React.",
        "job_id": "JOB_1",
        "include_llm": False,
    }
    first = client.post("/api/v1/matches/job", json=payload)
    second = client.post("/api/v1/matches/job", json=payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "usage_quota_exceeded"
    events = _audit_events(caplog)
    repeated = next(
        event
        for event in events
        if event["event_type"] == "security.matching.repeated"
    )
    assert repeated["metadata"]["operation"] == "job_match"


def test_recommendations_quota_and_repeated_matching_audit(
    client_factory, caplog
):
    client = client_factory(
        user_quota_recommendations_attempts=1,
        user_quota_recommendations_window_seconds=300,
    )
    register_user(client)
    caplog.set_level(logging.INFO, logger="jobmatch.audit")

    payload = {"resume_text": "Python developer using Docker and React."}
    first = client.post("/api/v1/matches/recommendations", json=payload)
    second = client.post("/api/v1/matches/recommendations", json=payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "usage_quota_exceeded"
    events = _audit_events(caplog)
    repeated = next(
        event
        for event in events
        if event["event_type"] == "security.matching.repeated"
    )
    assert repeated["metadata"]["operation"] == "recommendations"


def test_remote_llm_quota_is_enforced_when_provider_is_available(
    client_factory, monkeypatch, caplog
):
    client = client_factory(
        allow_remote_llm_analysis=True,
        user_quota_job_match_attempts=10,
        user_quota_job_match_window_seconds=300,
        user_quota_remote_llm_attempts=1,
        user_quota_remote_llm_window_seconds=300,
    )
    register_user(client)
    monkeypatch.setattr(
        matching_routes,
        "get_llm_provider",
        lambda: MockLLMProvider(),
    )
    caplog.set_level(logging.INFO, logger="jobmatch.audit")

    payload = {
        "resume_text": "Python engineer using Docker.",
        "job_id": "JOB_1",
        "include_llm": True,
        "allow_remote_analysis": True,
    }
    first = client.post("/api/v1/matches/job", json=payload)
    second = client.post("/api/v1/matches/job", json=payload)

    assert first.status_code == 200
    assert first.json()["llm_authorized"] is True
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "usage_quota_exceeded"
    events = _audit_events(caplog)
    llm_event = next(
        event
        for event in events
        if event["event_type"] == "security.llm.quota.rejected"
    )
    assert llm_event["metadata"]["operation"] == "remote_llm"


def test_user_quota_is_isolated_between_accounts(client_factory):
    first_client = client_factory(
        user_quota_cv_analysis_attempts=1,
        user_quota_cv_analysis_window_seconds=300,
    )
    second_client = client_factory(
        user_quota_cv_analysis_attempts=1,
        user_quota_cv_analysis_window_seconds=300,
    )
    register_user(first_client)
    register_user(second_client)

    payload = {"resume_text": "Python developer using Docker and React."}
    assert first_client.post("/api/v1/cv/analyze", json=payload).status_code == 200
    assert second_client.post("/api/v1/cv/analyze", json=payload).status_code == 200
    assert first_client.post("/api/v1/cv/analyze", json=payload).status_code == 429


def test_zero_quota_limit_disables_the_quota(client_factory):
    client = client_factory(
        user_quota_cv_analysis_attempts=0,
        user_quota_cv_analysis_window_seconds=300,
    )
    register_user(client)

    payload = {"resume_text": "Python developer using Docker and React."}
    assert client.post("/api/v1/cv/analyze", json=payload).status_code == 200
    assert client.post("/api/v1/cv/analyze", json=payload).status_code == 200


def test_rate_limiter_enforces_limits_under_concurrency():
    limiter = SlidingWindowRateLimiter(max_keys=100)

    def check(_: int) -> bool:
        return limiter.check("quota:cv_analysis:user", limit=5, window_seconds=60).allowed

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(check, range(20)))

    assert sum(results) == 5


def test_quota_settings_are_bounded():
    settings = APISettings.from_env(
        {
            "USER_QUOTA_CV_ANALYSIS_ATTEMPTS": "0",
            "USER_QUOTA_CV_ANALYSIS_WINDOW_SECONDS": "60",
            "COMMON_PASSWORD_CHECK_ENABLED": "false",
        }
    )
    assert settings.user_quota_cv_analysis_attempts == 0
    assert settings.common_password_check_enabled is False

    with pytest.raises(APIConfigurationError):
        APISettings.from_env({"USER_QUOTA_CV_ANALYSIS_WINDOW_SECONDS": "0"})
    with pytest.raises(APIConfigurationError):
        APISettings.from_env({"COMMON_PASSWORD_CHECK_ENABLED": "sometimes"})
