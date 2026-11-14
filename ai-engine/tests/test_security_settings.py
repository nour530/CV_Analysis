from __future__ import annotations

import pytest

from app.api.settings import (
    APIConfigurationError,
    APISettings,
    get_api_settings,
)
from app.auth.settings import AuthSettings, get_auth_settings
from app.main import create_app

STRONG_JWT_SECRET = "A7f!k9Qm2#Vx8Lp4$Zr6Nw1@Tb5Yc3Hd"


def test_placeholder_jwt_secrets_are_rejected() -> None:
    placeholders = [
        "replace-with-a-random-secret-of-at-least-32-characters",
        "replace-with-a-long-random-secret",
        "change-me-please-use-a-real-secret-value",
    ]

    for secret in placeholders:
        with pytest.raises(APIConfigurationError, match="placeholder"):
            AuthSettings.from_env(
                {
                    "APP_ENV": "development",
                    "JWT_SECRET_KEY": secret,
                }
            )


def test_production_auth_settings_require_secure_cookies_and_entropy() -> None:
    with pytest.raises(APIConfigurationError, match="COOKIE_SECURE"):
        AuthSettings.from_env(
            {
                "APP_ENV": "production",
                "JWT_SECRET_KEY": STRONG_JWT_SECRET,
            }
        )

    with pytest.raises(APIConfigurationError, match="entropy"):
        AuthSettings.from_env(
            {
                "APP_ENV": "production",
                "JWT_SECRET_KEY": "a" * 48,
                "COOKIE_SECURE": "true",
            }
        )

    settings = AuthSettings.from_env(
        {
            "APP_ENV": "production",
            "JWT_SECRET_KEY": STRONG_JWT_SECRET,
            "COOKIE_SECURE": "true",
        }
    )
    assert settings.app_env == "production"
    assert settings.cookie_secure is True


def test_production_api_settings_require_https_cors_and_hsts() -> None:
    base = {
        "APP_ENV": "production",
        "CORS_ORIGINS": "https://app.example.com",
        "ENABLE_HSTS": "true",
        "RATE_LIMIT_BACKEND": "memory",
    }

    settings = APISettings.from_env(base)
    assert settings.is_production is True
    assert settings.enable_api_docs is False

    with pytest.raises(APIConfigurationError, match="ENABLE_HSTS"):
        APISettings.from_env({**base, "ENABLE_HSTS": "false"})

    with pytest.raises(APIConfigurationError, match="https"):
        APISettings.from_env({**base, "CORS_ORIGINS": "http://app.example.com"})

    with pytest.raises(APIConfigurationError, match="localhost"):
        APISettings.from_env({**base, "CORS_ORIGINS": "https://localhost:4200"})

    docs_enabled = APISettings.from_env({**base, "ENABLE_API_DOCS": "true"})
    assert docs_enabled.enable_api_docs is True


def test_data_lifecycle_limits_are_configurable() -> None:
    settings = APISettings.from_env(
        {
            "MAX_REQUEST_BODY_BYTES": "1234567",
            "MAX_PDF_PAGES": "25",
            "MAX_DOCX_ENTRIES": "500",
            "MAX_DOCX_UNCOMPRESSED_BYTES": "9000000",
            "MAX_DOCX_COMPRESSION_RATIO": "50",
            "SESSION_CLEANUP_RETENTION_SECONDS": "3600",
            "REFRESH_TOKEN_CLEANUP_RETENTION_SECONDS": "7200",
        }
    )

    assert settings.max_request_body_bytes == 1_234_567
    assert settings.max_pdf_pages == 25
    assert settings.max_docx_entries == 500
    assert settings.max_docx_uncompressed_bytes == 9_000_000
    assert settings.max_docx_compression_ratio == 50
    assert settings.session_cleanup_retention_seconds == 3_600
    assert settings.refresh_token_cleanup_retention_seconds == 7_200


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_REQUEST_BODY_BYTES", "0"),
        ("MAX_PDF_PAGES", "0"),
        ("MAX_DOCX_ENTRIES", "0"),
        ("MAX_DOCX_UNCOMPRESSED_BYTES", "0"),
        ("MAX_DOCX_COMPRESSION_RATIO", "0"),
        ("SESSION_CLEANUP_RETENTION_SECONDS", "-1"),
        ("REFRESH_TOKEN_CLEANUP_RETENTION_SECONDS", "-1"),
    ],
)
def test_data_lifecycle_limits_reject_out_of_range_values(
    name: str,
    value: str,
) -> None:
    with pytest.raises(APIConfigurationError, match=name):
        APISettings.from_env({name: value})


def test_invalid_app_environment_is_rejected() -> None:
    with pytest.raises(APIConfigurationError, match="APP_ENV"):
        APISettings.from_env({"APP_ENV": "staging"})


def test_create_app_disables_docs_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", STRONG_JWT_SECRET)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("ENABLE_HSTS", "true")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://jobmatch:test@localhost:5432/jobmatch",
    )
    get_api_settings.cache_clear()
    get_auth_settings.cache_clear()

    try:
        production_app = create_app()
    finally:
        get_api_settings.cache_clear()
        get_auth_settings.cache_clear()

    assert production_app.docs_url is None
    assert production_app.redoc_url is None
    assert production_app.openapi_url is None
