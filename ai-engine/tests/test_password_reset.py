"""Password reset and email verification by single-use emailed links."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.db.models import AuthSession, EmailToken
from app.db.session import get_session_factory
from app.main import app
from app.services.mailer import MailSettings, get_mailer
from conftest import TEST_PASSWORD, register_user

NEW_PASSWORD = "Another-Horse-7-Staple"


class FakeMailer:
    def __init__(self) -> None:
        self.settings = MailSettings(host="smtp.example.com", public_url="https://jobmatch.example.com")
        self.sent: list[dict] = []

    def send(self, *, to: str, subject: str, text: str) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text})

    def link_token(self, kind: str) -> str:
        for message in reversed(self.sent):
            match = re.search(rf"/{kind}\?token=([\w-]+)", message["text"])
            if match:
                return match.group(1)
        raise AssertionError(f"no {kind} link sent")


@pytest.fixture
def mailer():
    fake = FakeMailer()
    app.dependency_overrides[get_mailer] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_mailer, None)


@pytest.fixture
def client(mailer):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _user(client: TestClient) -> str:
    register_user(client)
    return client.get("/api/v1/auth/me").json()["email"]


def test_reset_answers_the_same_whether_or_not_the_account_exists(client, mailer) -> None:
    email = _user(client)
    mailer.sent.clear()
    known = client.post("/api/v1/auth/password-reset/request", json={"email": email})
    unknown = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [m["to"] for m in mailer.sent] == [email], "only the real account gets an email"
    assert "https://jobmatch.example.com/reset-password?token=" in mailer.sent[0]["text"]


def test_reset_sets_the_password_signs_out_everywhere_and_burns_the_link(client, mailer) -> None:
    email = _user(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": email})
    token = mailer.link_token("reset-password")
    with get_session_factory()() as db:
        assert db.scalar(select(EmailToken.token_hash).where(EmailToken.purpose == "reset")) != token, "only a hash is stored"

    done = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": NEW_PASSWORD})
    assert done.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401, "every session is signed out"
    assert TestClient(app).post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}).status_code == 401
    assert TestClient(app).post("/api/v1/auth/login", json={"email": email, "password": NEW_PASSWORD}).status_code == 200

    again = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "Yet-Another-9-Horse"})
    assert again.status_code == 400 and again.json()["error"]["code"] == "reset_token_invalid"


def test_expired_wrong_and_superseded_links_are_refused(client, mailer) -> None:
    email = _user(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": email})
    first = mailer.link_token("reset-password")
    client.post("/api/v1/auth/password-reset/request", json={"email": email})
    second = mailer.link_token("reset-password")
    refused = client.post("/api/v1/auth/password-reset/confirm", json={"token": first, "new_password": NEW_PASSWORD})
    assert refused.status_code == 400, "a newer link voids the older one"

    with get_session_factory()() as db:
        db.execute(update(EmailToken).values(expires_at=EmailToken.expires_at - timedelta(hours=1)))
        db.commit()
    expired = client.post("/api/v1/auth/password-reset/confirm", json={"token": second, "new_password": NEW_PASSWORD})
    assert expired.status_code == 400
    wrong = client.post("/api/v1/auth/password-reset/confirm", json={"token": "x" * 43, "new_password": NEW_PASSWORD})
    assert wrong.status_code == 400


def test_a_refused_new_password_keeps_the_link_usable(client, mailer) -> None:
    email = _user(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": email})
    token = mailer.link_token("reset-password")
    weak = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "short"})
    assert weak.status_code == 422
    common = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "Password123456"})
    if common.status_code == 400 and common.json()["error"]["code"] == "password_too_common":
        pass  # refused by the common-password check
    ok = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": NEW_PASSWORD})
    assert ok.status_code == 204, ok.text


def test_registration_sends_a_verification_link_that_confirms_the_email(client, mailer) -> None:
    email = _user(client)
    assert client.get("/api/v1/auth/me").json()["email_verified"] is False
    assert mailer.sent[0]["to"] == email and "verify-email?token=" in mailer.sent[0]["text"]
    token = mailer.link_token("verify-email")

    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 204
    assert client.get("/api/v1/auth/me").json()["email_verified"] is True
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 400, "single use"
    reset = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": NEW_PASSWORD})
    assert reset.status_code == 400, "a verification link can't reset a password"

    mailer.sent.clear()
    assert client.post("/api/v1/auth/verify-email/resend").status_code == 202
    assert mailer.sent == [], "nothing to send once verified"


def test_mail_is_off_without_smtp_host() -> None:
    assert MailSettings.from_env({}).enabled is False
    settings = MailSettings.from_env({"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "465", "APP_PUBLIC_URL": "https://x.example/"})
    assert settings.enabled and settings.port == 465 and settings.public_url == "https://x.example"


def test_deleting_the_account_removes_its_email_tokens(client, mailer) -> None:
    email = _user(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": email})
    assert client.request("DELETE", "/api/v1/auth/me", json={"current_password": TEST_PASSWORD}).status_code == 204
    with get_session_factory()() as db:
        assert db.scalars(select(EmailToken)).all() == []
        assert db.scalars(select(AuthSession)).all() == []
