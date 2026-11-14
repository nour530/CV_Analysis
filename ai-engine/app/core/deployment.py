from __future__ import annotations

from app.api.settings import APIConfigurationError, APISettings
from app.auth.settings import AuthSettings
from app.core.logging import log_operational_event
from app.db.settings import DatabaseSettings


def validate_deployment_configuration(
    api_settings: APISettings,
    auth_settings: AuthSettings,
) -> None:
    """Fail fast on production settings that are unsafe for deployment."""
    if not api_settings.is_production:
        return

    database = DatabaseSettings.from_env()
    if database.is_sqlite:
        raise APIConfigurationError(
            "Production requires a PostgreSQL DATABASE_URL."
        )

    if api_settings.rate_limit_backend != "redis":
        log_operational_event(
            "configuration_warning",
            warning="memory_rate_limit_backend",
            environment=api_settings.app_env,
        )
    if not api_settings.trusted_proxy_networks:
        log_operational_event(
            "configuration_warning",
            warning="trusted_proxy_ips_not_configured",
            environment=api_settings.app_env,
        )
    if auth_settings.cookie_samesite == "none" and not auth_settings.cookie_secure:
        raise APIConfigurationError(
            "SameSite=None cookies require COOKIE_SECURE=true."
        )
