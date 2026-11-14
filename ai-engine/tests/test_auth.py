from __future__ import annotations

import csv
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.dependencies import get_api_settings
from app.api.settings import APISettings
from app.auth.service import AuthService
from app.auth.settings import get_auth_settings
from app.db.models import User, UserSettings
from app.db.session import get_session_factory
from app.main import app
from app.services.user_records import get_or_create_user_settings
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
        "clean_description": "Build backend services with Python and Docker.",
        "job_title": "Backend Engineer",
        "required_skills": "Python",
        "preferred_skills": "Docker",
        "other_skills": "",
        "all_extracted_skills": "Python;Docker",
        "responsibilities": "Build APIs",
        "qualifications": "3+ years experience",
    }
]


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
        options = {"jobs_features_path": catalog_path}
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


@pytest.fixture
def client(client_factory) -> TestClient:
    return client_factory()


def _new_email() -> str:
    return f"user-{uuid.uuid4().hex}@example.com"


def _credentials(email: str, password: str = TEST_PASSWORD) -> dict:
    return {"email": email, "password": password}


def test_registration_is_neutral_and_login_sets_secure_cookie_contract(client):
    email = _new_email()
    settings = get_auth_settings()
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Ada Lovelace",
            "email": email.upper(),
            "password": TEST_PASSWORD,
        },
    )

    assert registration.status_code == 202
    assert registration.headers["cache-control"] == "no-store"
    assert registration.json() == {
        "message": "If this email is available, the account can now sign in."
    }
    assert "access_token" not in registration.text
    assert "password" not in registration.text
    assert settings.refresh_cookie_name not in registration.cookies
    assert settings.csrf_cookie_name not in registration.cookies

    response = client.post("/api/v1/auth/login", json=_credentials(email))
    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["csrf_token"]
    assert payload["user"]["email"] == email
    assert payload["user"]["full_name"] == "Ada Lovelace"
    assert "password" not in response.text
    assert "password_hash" not in response.text

    refresh_cookie = response.cookies.get(settings.refresh_cookie_name)
    csrf_cookie = response.cookies.get(settings.csrf_cookie_name)
    assert refresh_cookie
    assert csrf_cookie
    set_cookie_headers = response.headers.get_list("set-cookie")
    refresh_header = next(
        header for header in set_cookie_headers if settings.refresh_cookie_name in header
    )
    assert "HttpOnly" in refresh_header
    assert "SameSite=strict" in refresh_header
    assert "Path=/api/v1/auth" in refresh_header


def test_registration_does_not_reveal_existing_accounts_and_keeps_validation(client):
    email = _new_email()
    first_password = TEST_PASSWORD
    duplicate_password = "Different-Horse-9-Battery"
    first = client.post(
        "/api/v1/auth/register",
        json={"full_name": "One User", "email": email, "password": first_password},
    )
    assert first.status_code == 202

    duplicate = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Two User",
            "email": email.upper(),
            "password": duplicate_password,
        },
    )
    assert duplicate.status_code == 202
    assert duplicate.json() == first.json()
    assert "access_token" not in duplicate.text

    original_login = client.post(
        "/api/v1/auth/login",
        json=_credentials(email, first_password),
    )
    assert original_login.status_code == 200
    duplicate_login = client.post(
        "/api/v1/auth/login",
        json=_credentials(email, duplicate_password),
    )
    assert duplicate_login.status_code == 401
    assert duplicate_login.json()["error"]["code"] == "invalid_credentials"

    weak = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Weak User", "email": _new_email(), "password": "short"},
    )
    assert weak.status_code == 422
    assert "short" not in weak.text

    invalid_email = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Bad Email",
            "email": "not-an-email",
            "password": TEST_PASSWORD,
        },
    )
    assert invalid_email.status_code == 422


def test_login_is_generic_and_protected_routes_require_access_token(client):
    email = _new_email()
    register_user(client, email=email)
    client.headers.pop("Authorization")

    unauthenticated = client.get("/api/v1/auth/me")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    assert unauthenticated.headers["cache-control"] == "no-store"
    assert unauthenticated.json()["error"]["code"] == "authentication_required"

    wrong_password = client.post(
        "/api/v1/auth/login",
        json=_credentials(email, "Wrong-Password-9"),
    )
    unknown_user = client.post(
        "/api/v1/auth/login",
        json=_credentials(_new_email(), TEST_PASSWORD),
    )
    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"
    assert unknown_user.json()["error"]["code"] == "invalid_credentials"
    assert (
        wrong_password.json()["error"]["message"]
        == unknown_user.json()["error"]["message"]
        == "Invalid email or password."
    )

    login = client.post("/api/v1/auth/login", json=_credentials(email))
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_profile_updates_are_user_scoped_and_not_cached(client_factory):
    first = client_factory()
    register_user(first, email="profile-one@example.com")

    updated = first.patch(
        "/api/v1/auth/me",
        json={"full_name": "Updated Name"},
    )
    assert updated.status_code == 200
    assert updated.headers["cache-control"] == "no-store"
    assert updated.json()["full_name"] == "Updated Name"

    csrf = first.cookies.get(get_auth_settings().csrf_cookie_name)
    changed = first.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": "Another-Correct-Horse-8-Battery",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 204
    assert changed.headers["cache-control"] == "no-store"

    current = first.get("/api/v1/auth/me")
    assert current.status_code == 200
    assert current.headers["cache-control"] == "no-store"
    assert current.json()["full_name"] == "Updated Name"

    second = client_factory()
    register_user(second, email="profile-two@example.com")
    other = second.get("/api/v1/auth/me")
    assert other.status_code == 200
    assert other.json()["full_name"] == "Test User"


def test_client_supplied_user_ids_are_rejected(client):
    register_user(client)

    settings = client.put(
        "/api/v1/account/settings",
        json={"recommendation_limit": 7, "user_id": "attacker-supplied-id"},
    )
    assert settings.status_code == 422
    assert "attacker-supplied-id" not in settings.text


def test_disabled_account_is_rejected(client):
    email = _new_email()
    payload = register_user(client, email=email)
    session_factory = get_session_factory()
    with session_factory() as db:
        user = db.get(User, payload["user"]["id"])
        assert user is not None
        user.is_active = False
        db.commit()

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401
    login = client.post("/api/v1/auth/login", json=_credentials(email))
    assert login.status_code == 401
    assert login.json()["error"]["code"] == "invalid_credentials"
    assert login.json()["error"]["message"] == "Invalid email or password."


def test_refresh_rotation_and_reuse_detection(client):
    register_user(client)
    settings = get_auth_settings()
    old_refresh = client.cookies.get(settings.refresh_cookie_name)
    old_csrf = client.cookies.get(settings.csrf_cookie_name)
    assert old_refresh and old_csrf

    refreshed = client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": old_csrf},
    )
    assert refreshed.status_code == 200
    new_refresh = refreshed.json()["csrf_token"]
    assert refreshed.cookies.get(settings.refresh_cookie_name) != old_refresh
    assert new_refresh != old_csrf

    new_access = refreshed.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {new_access}"
    assert client.get("/api/v1/auth/me").status_code == 200

    client.cookies.clear()
    client.cookies.set(
        settings.refresh_cookie_name,
        old_refresh,
        path="/api/v1/auth",
    )
    client.cookies.set(settings.csrf_cookie_name, old_csrf, path="/")
    replay = client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": old_csrf},
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_token_reused"

    revoked = client.get("/api/v1/auth/me")
    assert revoked.status_code == 401


def test_refresh_requires_csrf_and_logout_revokes_session(client):
    register_user(client)
    settings = get_auth_settings()
    access_token = client.headers["Authorization"]
    client.headers.pop("X-CSRF-Token")

    csrf_missing = client.post("/api/v1/auth/refresh")
    assert csrf_missing.status_code == 403
    assert csrf_missing.headers["cache-control"] == "no-store"
    assert csrf_missing.json()["error"]["code"] == "csrf_failed"

    csrf = client.cookies.get(settings.csrf_cookie_name)
    logout = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf},
    )
    assert logout.status_code == 204
    assert not client.cookies.get(settings.refresh_cookie_name)

    client.headers["Authorization"] = access_token
    assert client.get("/api/v1/auth/me").status_code == 401


def test_change_password_revokes_other_sessions(client_factory):
    email = _new_email()
    first = client_factory()
    register_user(first, email=email)

    second = client_factory()
    login = second.post("/api/v1/auth/login", json=_credentials(email))
    assert login.status_code == 200
    second.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

    csrf = first.cookies.get(get_auth_settings().csrf_cookie_name)
    new_password = "New-Correct-Horse-7-Battery"
    changed = first.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": new_password},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 204
    assert second.get("/api/v1/auth/me").status_code == 401
    assert first.get("/api/v1/auth/me").status_code == 200

    old_login = second.post("/api/v1/auth/login", json=_credentials(email))
    assert old_login.status_code == 401
    new_login = second.post(
        "/api/v1/auth/login",
        json=_credentials(email, new_password),
    )
    assert new_login.status_code == 200


def test_cv_history_and_latest_are_user_scoped(client_factory):
    first = client_factory()
    register_user(first, email="first@example.com")
    created = first.post(
        "/api/v1/cv/analyze",
        json={
            "resume_text": "Python developer using Docker and PostgreSQL.",
            "resume_id": "first-resume",
        },
    )
    assert created.status_code == 200
    analysis_id = created.json()["analysis_id"]
    assert analysis_id

    latest = first.get("/api/v1/cv/latest")
    assert latest.status_code == 200
    assert latest.json()["analysis_id"] == analysis_id
    history = first.get("/api/v1/cv/history").json()["items"]
    assert [item["analysis_id"] for item in history] == [analysis_id]

    second = client_factory()
    register_user(second, email="second@example.com")
    assert second.get("/api/v1/cv/latest").status_code == 404
    assert second.get("/api/v1/cv/history").json()["items"] == []
    assert second.get(f"/api/v1/cv/history/{analysis_id}").status_code == 404


def test_saved_matches_are_user_scoped_and_include_match_id(client_factory):
    first = client_factory()
    register_user(first, email="matcher-one@example.com")
    created = first.post(
        "/api/v1/matches/job",
        json={
            "resume_text": "Python developer using Docker.",
            "job_id": "JOB_1",
        },
    )
    assert created.status_code == 200
    match_id = created.json()["match_id"]
    assert match_id

    history = first.get("/api/v1/matches/history").json()["items"]
    assert [item["match_id"] for item in history] == [match_id]
    assert first.get(f"/api/v1/matches/history/{match_id}").status_code == 200

    second = client_factory()
    register_user(second, email="matcher-two@example.com")
    assert second.get(f"/api/v1/matches/history/{match_id}").status_code == 404
    assert second.get("/api/v1/matches/history").json()["items"] == []


def test_account_settings_are_persisted_per_user(client_factory):
    first = client_factory()
    register_user(first, email="settings-one@example.com")
    initial = first.get("/api/v1/account/settings")
    assert initial.status_code == 200
    assert initial.json()["recommendation_limit"] == 10

    updated = first.put(
        "/api/v1/account/settings",
        json={
            "recommendation_limit": 7,
            "candidate_pool_size": 33,
            "preferred_category": "Data Science",
            "allow_remote_analysis": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["recommendation_limit"] == 7
    assert updated.json()["allow_remote_analysis"] is True
    assert first.get("/api/v1/account/settings").json()["candidate_pool_size"] == 33

    second = client_factory()
    register_user(second, email="settings-two@example.com")
    other = second.get("/api/v1/account/settings")
    assert other.status_code == 200
    assert other.json()["recommendation_limit"] == 10


def test_login_rate_limit_returns_retry_after(client_factory):
    client = client_factory(
        login_rate_limit_attempts=2,
        login_rate_limit_window_seconds=300,
    )
    email = _new_email()
    register_user(client, email=email)
    client.headers.pop("Authorization")

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/login",
            json=_credentials(email, "Wrong-Password-9"),
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/v1/auth/login",
        json=_credentials(email, "Wrong-Password-9"),
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert float(limited.headers["retry-after"]) >= 1


def test_registration_rate_limit_returns_retry_after(client_factory):
    client = client_factory(
        registration_rate_limit_attempts=2,
        registration_rate_limit_window_seconds=300,
    )

    for index in range(2):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "full_name": f"Rate User {index}",
                "email": _new_email(),
                "password": TEST_PASSWORD,
            },
        )
        assert response.status_code == 202

    limited = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Rate User Limited",
            "email": _new_email(),
            "password": TEST_PASSWORD,
        },
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert float(limited.headers["retry-after"]) >= 1


def test_refresh_rate_limit_returns_retry_after(client_factory):
    client = client_factory(
        refresh_rate_limit_attempts=1,
        refresh_rate_limit_window_seconds=300,
    )
    register_user(client)

    for _ in range(4):
        csrf = client.cookies.get(get_auth_settings().csrf_cookie_name)
        response = client.post(
            "/api/v1/auth/refresh",
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    csrf = client.cookies.get(get_auth_settings().csrf_cookie_name)
    limited = client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": csrf},
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert float(limited.headers["retry-after"]) >= 1


def test_change_password_rate_limit_returns_retry_after(client_factory):
    client = client_factory(
        password_change_rate_limit_attempts=2,
        password_change_rate_limit_window_seconds=300,
    )
    register_user(client)

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "Wrong-Password-9",
                "new_password": "New-Correct-Horse-7-Battery",
            },
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_current_password"

    limited = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "Wrong-Password-9",
            "new_password": "New-Correct-Horse-7-Battery",
        },
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert float(limited.headers["retry-after"]) >= 1


def test_security_headers_and_credentialed_cors(client):
    response = client.get(
        "/health",
        headers={"Origin": "http://localhost:4200"},
    )
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-origin"] == "http://localhost:4200"

    preflight = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:4200",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert preflight.status_code == 200
    assert "x-csrf-token" in preflight.headers["access-control-allow-headers"].lower()


def test_concurrent_refresh_allows_only_one_rotation(client_factory):
    first = client_factory()
    register_user(first, email=_new_email())
    settings = get_auth_settings()
    refresh_token = first.cookies.get(settings.refresh_cookie_name)
    csrf_token = first.cookies.get(settings.csrf_cookie_name)
    assert refresh_token and csrf_token

    second = client_factory()
    for client in (first, second):
        client.cookies.set(
            settings.refresh_cookie_name,
            refresh_token,
            path=settings.cookie_path,
        )
        client.cookies.set(
            settings.csrf_cookie_name,
            csrf_token,
            path=settings.csrf_cookie_path,
        )

    barrier = threading.Barrier(2)

    def refresh(client: TestClient):
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/auth/refresh",
            headers={"X-CSRF-Token": csrf_token},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(refresh, (first, second)))

    assert sorted(response.status_code for response in responses) == [200, 401]
    winner = next(response for response in responses if response.status_code == 200)
    new_refresh = winner.cookies.get(settings.refresh_cookie_name)
    new_csrf = winner.cookies.get(settings.csrf_cookie_name)
    assert new_refresh and new_csrf
    assert new_refresh != refresh_token

    replay_client = client_factory()
    replay_client.cookies.set(
        settings.refresh_cookie_name,
        new_refresh,
        path=settings.cookie_path,
    )
    replay_client.cookies.set(
        settings.csrf_cookie_name,
        new_csrf,
        path=settings.csrf_cookie_path,
    )
    replay = replay_client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": new_csrf},
    )
    assert replay.status_code == 401


def test_concurrent_duplicate_registration_creates_one_account(monkeypatch):
    email = _new_email()
    session_factory = get_session_factory()
    barrier = threading.Barrier(2)
    original_get_user_by_email = AuthService.get_user_by_email

    def synchronized_lookup(self, candidate_email: str):
        user = original_get_user_by_email(self, candidate_email)
        if user is None:
            barrier.wait(timeout=5)
        return user

    monkeypatch.setattr(AuthService, "get_user_by_email", synchronized_lookup)

    def register_once():
        with session_factory() as db:
            return AuthService(db, get_auth_settings()).register(
                full_name="Race User",
                email=email,
                password=TEST_PASSWORD,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: register_once(), range(2)))

    assert sum(user is not None for user in results) == 1
    with session_factory() as db:
        users = db.scalars(select(User).where(User.email == email)).all()
        assert len(users) == 1
        assert db.get(UserSettings, users[0].id) is not None


def test_concurrent_user_settings_creation_returns_one_row():
    session_factory = get_session_factory()
    with session_factory() as db:
        user = User(
            full_name="Settings Race",
            email=_new_email(),
            password_hash="unused",
            is_active=True,
        )
        db.add(user)
        db.commit()
        user_id = user.id

    barrier = threading.Barrier(2)

    def load_settings():
        with session_factory() as db:
            barrier.wait(timeout=5)
            return get_or_create_user_settings(db, user_id=user_id).user_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: load_settings(), range(2)))

    assert results == [user_id, user_id]
    with session_factory() as db:
        count = db.scalar(
            select(func.count())
            .select_from(UserSettings)
            .where(UserSettings.user_id == user_id)
        )
        assert count == 1


def test_get_or_create_user_settings_recovers_from_concurrent_insert(monkeypatch):
    session_factory = get_session_factory()
    with session_factory() as db:
        user = User(
            full_name="Settings Recovery",
            email=_new_email(),
            password_hash="unused",
            is_active=True,
        )
        db.add(user)
        db.commit()
        user_id = user.id

    with session_factory() as db:
        db.add(UserSettings(user_id=user_id))
        db.commit()

    with session_factory() as db:
        original_get = db.get
        calls = 0

        def stale_get(entity, ident, *args, **kwargs):
            nonlocal calls
            if entity is UserSettings and calls == 0:
                calls += 1
                return None
            return original_get(entity, ident, *args, **kwargs)

        monkeypatch.setattr(db, "get", stale_get)
        settings = get_or_create_user_settings(db, user_id=user_id)
        assert settings.user_id == user_id

    with session_factory() as db:
        count = db.scalar(
            select(func.count())
            .select_from(UserSettings)
            .where(UserSettings.user_id == user_id)
        )
        assert count == 1


def test_logout_csrf_failure_preserves_the_server_session(client):
    register_user(client)
    settings = get_auth_settings()
    access_token = client.headers["Authorization"]
    refresh_token = client.cookies.get(settings.refresh_cookie_name)
    csrf_token = client.cookies.get(settings.csrf_cookie_name)
    assert access_token and refresh_token and csrf_token

    client.headers.pop("X-CSRF-Token")
    missing = client.post("/api/v1/auth/logout")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_failed"
    assert missing.headers.get_list("set-cookie") == []
    assert client.cookies.get(settings.refresh_cookie_name) == refresh_token
    assert client.get("/api/v1/auth/me").status_code == 200

    invalid = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": "not-the-csrf-token"},
    )
    assert invalid.status_code == 403
    assert invalid.json()["error"]["code"] == "csrf_failed"
    assert invalid.headers.get_list("set-cookie") == []
    assert client.cookies.get(settings.refresh_cookie_name) == refresh_token
    assert client.get("/api/v1/auth/me").status_code == 200
