from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

AI_ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(AI_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_DIR))

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="jobmatch-tests-"))
os.environ["APP_ENV"] = "test"
# SQLite by default; CI also runs the suite against PostgreSQL, the production
# database, by setting TEST_DATABASE_URL (see .github/workflows/ci.yml).
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_TEST_DB_DIR / 'test.db').as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-only-jwt-secret-key-with-at-least-32-characters"
os.environ["COOKIE_SECURE"] = "false"
os.environ["COOKIE_SAMESITE"] = "strict"

from app.api.settings import get_api_settings  # noqa: E402
from app.core.audit import reset_alert_state  # noqa: E402
from app.core.rate_limit import RATE_LIMITER, configure_rate_limiter  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.init import init_database  # noqa: E402
from app.db.session import get_engine  # noqa: E402
import app.db.models  # noqa: E402,F401

TEST_PASSWORD = "Correct-Horse-9-Battery"


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: optional live OpenAI integration tests",
    )


def register_user(
    client: TestClient,
    *,
    email: str | None = None,
    full_name: str = "Test User",
    password: str = TEST_PASSWORD,
) -> dict:
    email = email or f"user-{uuid.uuid4().hex}@example.com"
    registration = client.post(
        "/api/v1/auth/register",
        json={"full_name": full_name, "email": email, "password": password},
    )
    assert registration.status_code == 202, registration.text
    assert "access_token" not in registration.json()

    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    client.headers["Authorization"] = f"Bearer {payload['access_token']}"
    client.headers["X-CSRF-Token"] = payload["csrf_token"]
    return payload


@pytest.fixture
def auth_helper():
    return register_user


@pytest.fixture(autouse=True)
def clean_database():
    configure_rate_limiter(settings=get_api_settings())
    RATE_LIMITER.reset()
    reset_alert_state()
    Base.metadata.drop_all(bind=get_engine())
    init_database()
    yield
    RATE_LIMITER.reset()
    reset_alert_state()
