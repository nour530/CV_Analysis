"""Account deletion and data export (a user's right to their data)."""

from __future__ import annotations

import csv
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.dependencies import get_api_settings, reset_job_catalog_cache
from app.api.settings import APISettings
from app.db.models import (
    AuthSession,
    CandidateProfile,
    ChatMessage,
    CvDocumentRecord,
    CVAnalysis,
    JobApplication,
    RefreshToken,
    SavedMatch,
    User,
    UserSettings,
)
from app.db.session import get_session_factory
from app.main import app
from app.services import account_data
from conftest import TEST_PASSWORD, register_user
from cv_samples import STRONG_CV
from test_resumes import ROWS

USER_TABLES = (ChatMessage, JobApplication, CvDocumentRecord, SavedMatch, CVAnalysis, CandidateProfile, UserSettings, AuthSession)


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "job_features.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)
    settings = APISettings(jobs_features_path=path, jobs_catalog_source="dataset")
    app.dependency_overrides[get_api_settings] = lambda: settings
    reset_job_catalog_cache()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _fill(client: TestClient) -> str:
    """Register a user and give them a CV analysis, a CV document, a
    tracked application and settings. Returns their user id."""
    register_user(client)
    me = client.get("/api/v1/auth/me").json()
    assert client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV}).status_code == 200
    assert client.post("/api/v1/resumes", json={}).status_code == 201
    assert client.post("/api/v1/applications", json={"job_id": "JOB_1"}).status_code in (200, 201)
    client.put("/api/v1/account/settings", json={"recommendation_limit": 10, "candidate_pool_size": 30, "allow_remote_analysis": False})
    return me["id"]


def _count(model, user_id: str) -> int:
    column = model.user_id if hasattr(model, "user_id") else model.id
    with get_session_factory()() as db:
        return db.scalar(select(func.count()).select_from(model).where(column == user_id))


def test_export_returns_only_my_data_without_credentials(client) -> None:
    other = TestClient(app, raise_server_exceptions=False)
    other_id = _fill(other)
    my_id = _fill(client)

    response = client.get("/api/v1/account/export")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert data["account"]["id"] == my_id
    assert "password_hash" not in data["account"]
    assert data["cv_analyses"] and data["cv_documents"] and data["job_applications"] and data["user_settings"]
    assert data["cv_analyses"][0]["resume_text"].startswith("Amira Ben Salah")
    text = json.dumps(data)
    assert other_id not in text, "another user's data must never appear"
    assert "token_hash" not in text

    assert TestClient(app).get("/api/v1/account/export").status_code == 401


def test_delete_needs_the_current_password(client) -> None:
    user_id = _fill(client)
    wrong = client.request("DELETE", "/api/v1/auth/me", json={"current_password": "not-my-password"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_current_password"
    assert _count(User, user_id) == 1 and _count(CVAnalysis, user_id) == 1


def test_delete_removes_everything_and_ends_access(client) -> None:
    other = TestClient(app, raise_server_exceptions=False)
    other_id = _fill(other)
    user_id = _fill(client)
    email = client.get("/api/v1/auth/me").json()["email"]
    with get_session_factory()() as db:
        session_ids = db.scalars(select(AuthSession.id).where(AuthSession.user_id == user_id)).all()

    response = client.request("DELETE", "/api/v1/auth/me", json={"current_password": TEST_PASSWORD})
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"

    assert _count(User, user_id) == 0
    for model in USER_TABLES:
        assert _count(model, user_id) == 0, model.__tablename__
    with get_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(RefreshToken).where(RefreshToken.session_id.in_(session_ids))) == 0

    assert client.get("/api/v1/auth/me").status_code == 401, "the old access token must stop working"
    login = TestClient(app).post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert login.status_code == 401

    assert _count(User, other_id) == 1 and _count(CVAnalysis, other_id) == 1, "other users are untouched"
    assert other.get("/api/v1/account/export").status_code == 200


def test_delete_rolls_back_completely_when_a_step_fails(client, monkeypatch) -> None:
    user_id = _fill(client)

    with get_session_factory()() as db:
        real_execute = db.execute
        calls = {"n": 0}

        def failing_execute(statement, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 5:  # after some tables were already deleted
                raise RuntimeError("disk full")
            return real_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", failing_execute)
        with pytest.raises(RuntimeError):
            account_data.delete_account(db, user_id)

    assert _count(User, user_id) == 1
    assert _count(CVAnalysis, user_id) == 1 and _count(JobApplication, user_id) == 1 and _count(CvDocumentRecord, user_id) == 1
