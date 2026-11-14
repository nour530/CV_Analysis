from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Literal, Mapping, cast
from urllib.parse import urlsplit


class APIConfigurationError(RuntimeError):
    """Raised when backend API configuration is invalid."""


AI_ENGINE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_JOBS_FEATURES_PATH = AI_ENGINE_DIR / "data" / "features" / "job_features.csv"
DEFAULT_JOB_SOURCES_PATH = AI_ENGINE_DIR / "data" / "job_sources.json"
JOB_CATALOG_SOURCES = ("auto", "database", "dataset")

AppEnvironment = Literal["development", "test", "production"]
RateLimitBackendName = Literal["memory", "redis"]
RedisFailureMode = Literal["closed", "open"]
ALLOWED_ENVIRONMENTS = frozenset({"development", "test", "production"})
ALLOWED_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
LOCAL_ORIGIN_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_REDIS_KEY_PREFIX = "jobmatch:rate_limit:"


def read_app_environment(env: Mapping[str, str]) -> AppEnvironment:
    raw = (env.get("APP_ENV") or "development").strip().casefold()
    if raw not in ALLOWED_ENVIRONMENTS:
        raise APIConfigurationError(
            "APP_ENV must be development, test, or production."
        )
    return cast(AppEnvironment, raw)


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise APIConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise APIConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


def _bounded_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise APIConfigurationError(f"{name} must be a number.") from exc
    if not minimum <= value <= maximum:
        raise APIConfigurationError(
            f"{name} must be between {minimum:g} and {maximum:g}."
        )
    return value


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = (env.get(name) or "").strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise APIConfigurationError(f"{name} must be true or false.")


def _validate_cors_origin(origin: str) -> None:
    if "*" in origin:
        raise APIConfigurationError(
            "CORS_ORIGINS entries must not contain wildcards."
        )
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise APIConfigurationError(
            "CORS_ORIGINS entries must be absolute http(s) origins."
        )
    if parsed.username or parsed.password:
        raise APIConfigurationError(
            "CORS_ORIGINS entries must not contain credentials."
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise APIConfigurationError(
            "CORS_ORIGINS entries must not contain paths, queries, or fragments."
        )


def _cors_origins(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = env.get("CORS_ORIGINS")
    if raw is None or not raw.strip():
        return ("http://localhost:4200",)
    origins = tuple(
        origin.strip().rstrip("/")
        for origin in raw.split(",")
        if origin.strip()
    )
    if not origins:
        return ("http://localhost:4200",)
    if "*" in origins:
        raise APIConfigurationError(
            "CORS_ORIGINS cannot contain a wildcard when credentials are enabled."
        )
    for origin in origins:
        _validate_cors_origin(origin)
    return origins


def _validate_production_cors(origins: tuple[str, ...]) -> None:
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme != "https":
            raise APIConfigurationError(
                "CORS_ORIGINS must use https in production."
            )
        if (parsed.hostname or "").casefold() in LOCAL_ORIGIN_HOSTS:
            raise APIConfigurationError(
                "CORS_ORIGINS cannot use a localhost origin in production."
            )


def _rate_limit_backend(
    env: Mapping[str, str],
    app_env: AppEnvironment,
) -> tuple[RateLimitBackendName, str | None]:
    raw = (env.get("RATE_LIMIT_BACKEND") or "auto").strip().casefold()
    if raw not in {"auto", "memory", "redis"}:
        raise APIConfigurationError(
            "RATE_LIMIT_BACKEND must be auto, memory, or redis."
        )

    redis_url = (env.get("REDIS_URL") or "").strip() or None
    if redis_url is not None:
        parsed = urlsplit(redis_url)
        if parsed.scheme not in {"redis", "rediss", "unix"}:
            raise APIConfigurationError(
                "REDIS_URL must use the redis, rediss, or unix scheme."
            )
        if not parsed.netloc and not parsed.path:
            raise APIConfigurationError("REDIS_URL must include a host or socket path.")

    if raw == "auto":
        if redis_url is not None:
            return "redis", redis_url
        if app_env == "production":
            raise APIConfigurationError(
                "REDIS_URL is required in production when RATE_LIMIT_BACKEND=auto."
            )
        return "memory", None
    if raw == "redis":
        if redis_url is None:
            raise APIConfigurationError(
                "REDIS_URL is required when RATE_LIMIT_BACKEND=redis."
            )
        return "redis", redis_url
    return "memory", redis_url


def _trusted_proxy_networks(
    env: Mapping[str, str],
) -> tuple[IPv4Network | IPv6Network, ...]:
    raw = (env.get("TRUSTED_PROXY_IPS") or "").strip()
    if not raw:
        return ()
    networks: list[IPv4Network | IPv6Network] = []
    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        try:
            networks.append(ip_network(candidate, strict=False))
        except ValueError as exc:
            raise APIConfigurationError(
                "TRUSTED_PROXY_IPS must contain only IP addresses or CIDR ranges."
            ) from exc
    return tuple(networks)


@dataclass(frozen=True, slots=True)
class APISettings:
    app_name: str = "JobMatch AI API"
    app_version: str = "1.0.0"
    app_env: AppEnvironment = "development"
    api_prefix: str = "/api/v1"
    jobs_features_path: Path = DEFAULT_JOBS_FEATURES_PATH
    jobs_catalog_source: str = "auto"
    jobs_catalog_refresh_seconds: int = 300
    job_sources_path: Path = DEFAULT_JOB_SOURCES_PATH
    allow_remote_llm_analysis: bool = False
    cors_origins: tuple[str, ...] = ("http://localhost:4200",)
    max_text_length: int = 200_000
    max_upload_bytes: int = 5_000_000
    default_recommendation_limit: int = 10
    max_recommendation_limit: int = 50
    default_candidate_pool_size: int = 30
    max_candidate_pool_size: int = 200
    api_rate_limit_requests: int = 120
    api_rate_limit_window_seconds: int = 60
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 300
    registration_rate_limit_attempts: int = 5
    registration_rate_limit_window_seconds: int = 3600
    refresh_rate_limit_attempts: int = 30
    refresh_rate_limit_window_seconds: int = 60
    password_change_rate_limit_attempts: int = 5
    password_change_rate_limit_window_seconds: int = 300
    rate_limit_max_keys: int = 20_000
    rate_limit_backend: RateLimitBackendName = "memory"
    redis_url: str | None = None
    redis_key_prefix: str = DEFAULT_REDIS_KEY_PREFIX
    redis_connect_timeout_seconds: float = 1.0
    redis_socket_timeout_seconds: float = 1.0
    redis_health_check_interval_seconds: int = 30
    redis_max_connections: int = 100
    rate_limit_redis_failure_mode: RedisFailureMode = "closed"
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...] = ()
    enable_hsts: bool = False
    enable_api_docs: bool = True
    auto_create_tables: bool = False
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False
    max_request_body_bytes: int = 8_000_000
    max_pdf_pages: int = 100
    max_docx_entries: int = 2_000
    max_docx_uncompressed_bytes: int = 50_000_000
    max_docx_compression_ratio: int = 200
    session_cleanup_retention_seconds: int = 2_592_000
    refresh_token_cleanup_retention_seconds: int = 2_592_000
    user_quota_cv_analysis_attempts: int = 30
    user_quota_cv_analysis_window_seconds: int = 3_600
    user_quota_job_match_attempts: int = 60
    user_quota_job_match_window_seconds: int = 3_600
    user_quota_recommendations_attempts: int = 30
    user_quota_recommendations_window_seconds: int = 3_600
    user_quota_remote_llm_attempts: int = 20
    user_quota_remote_llm_window_seconds: int = 3_600
    common_password_check_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> APISettings:
        env = os.environ if environ is None else environ
        app_env = read_app_environment(env)
        jobs_path = (env.get("JOBS_FEATURES_PATH") or "").strip()
        sources_path = (env.get("JOB_SOURCES_PATH") or "").strip()
        catalog_source = (env.get("JOBS_CATALOG_SOURCE") or "auto").strip().casefold()
        if catalog_source not in JOB_CATALOG_SOURCES:
            raise APIConfigurationError(
                "JOBS_CATALOG_SOURCE must be one of: auto, database, dataset."
            )
        api_prefix = (env.get("API_PREFIX") or "/api/v1").strip()
        if not api_prefix.startswith("/"):
            raise APIConfigurationError("API_PREFIX must start with '/'.")
        api_prefix = api_prefix.rstrip("/") or "/api/v1"

        default_limit = _bounded_int(
            env,
            "DEFAULT_RECOMMENDATION_LIMIT",
            10,
            minimum=1,
            maximum=50,
        )
        max_limit = _bounded_int(
            env,
            "MAX_RECOMMENDATION_LIMIT",
            max(50, default_limit),
            minimum=1,
            maximum=200,
        )
        if default_limit > max_limit:
            raise APIConfigurationError(
                "DEFAULT_RECOMMENDATION_LIMIT cannot exceed MAX_RECOMMENDATION_LIMIT."
            )
        default_pool = _bounded_int(
            env,
            "DEFAULT_CANDIDATE_POOL_SIZE",
            30,
            minimum=1,
            maximum=200,
        )
        max_pool = _bounded_int(
            env,
            "MAX_CANDIDATE_POOL_SIZE",
            max(200, default_pool),
            minimum=1,
            maximum=500,
        )
        if default_pool > max_pool:
            raise APIConfigurationError(
                "DEFAULT_CANDIDATE_POOL_SIZE cannot exceed MAX_CANDIDATE_POOL_SIZE."
            )

        cors_origins = _cors_origins(env)
        trusted_proxy_networks = _trusted_proxy_networks(env)
        enable_hsts = _bool(env, "ENABLE_HSTS")
        enable_api_docs = _bool(
            env,
            "ENABLE_API_DOCS",
            default=app_env != "production",
        )
        auto_create_tables = _bool(env, "AUTO_CREATE_TABLES")
        debug = _bool(env, "APP_DEBUG")
        redis_key_prefix = (
            env.get("REDIS_KEY_PREFIX") or DEFAULT_REDIS_KEY_PREFIX
        ).strip()
        if (
            not redis_key_prefix
            or len(redis_key_prefix) > 100
            or any(character.isspace() for character in redis_key_prefix)
        ):
            raise APIConfigurationError(
                "REDIS_KEY_PREFIX must be 1-100 characters without whitespace."
            )
        redis_failure_mode = (
            env.get("RATE_LIMIT_REDIS_FAILURE_MODE") or "closed"
        ).strip().casefold()
        if redis_failure_mode not in {"closed", "open"}:
            raise APIConfigurationError(
                "RATE_LIMIT_REDIS_FAILURE_MODE must be closed or open."
            )
        log_level = (env.get("LOG_LEVEL") or "INFO").strip().upper()
        if log_level not in ALLOWED_LOG_LEVELS:
            raise APIConfigurationError(
                "LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG."
            )
        log_json = _bool(env, "LOG_JSON", default=app_env == "production")
        if app_env == "production":
            if not enable_hsts:
                raise APIConfigurationError(
                    "ENABLE_HSTS must be true in production."
                )
            if auto_create_tables:
                raise APIConfigurationError(
                    "AUTO_CREATE_TABLES must be false in production."
                )
            if debug:
                raise APIConfigurationError("APP_DEBUG must be false in production.")
            if redis_failure_mode != "closed":
                raise APIConfigurationError(
                    "RATE_LIMIT_REDIS_FAILURE_MODE must be closed in production."
                )
            _validate_production_cors(cors_origins)

        rate_limit_backend, redis_url = _rate_limit_backend(env, app_env)

        return cls(
            app_name=(env.get("API_TITLE") or "JobMatch AI API").strip(),
            app_version=(env.get("API_VERSION") or "1.0.0").strip(),
            app_env=app_env,
            api_prefix=api_prefix,
            jobs_features_path=(
                Path(jobs_path) if jobs_path else DEFAULT_JOBS_FEATURES_PATH
            ),
            jobs_catalog_source=catalog_source,
            jobs_catalog_refresh_seconds=_bounded_int(
                env,
                "JOBS_CATALOG_REFRESH_SECONDS",
                300,
                minimum=10,
                maximum=86_400,
            ),
            job_sources_path=(
                Path(sources_path) if sources_path else DEFAULT_JOB_SOURCES_PATH
            ),
            allow_remote_llm_analysis=_bool(env, "ALLOW_REMOTE_LLM_ANALYSIS"),
            cors_origins=cors_origins,
            max_text_length=_bounded_int(
                env,
                "MAX_TEXT_LENGTH",
                200_000,
                minimum=1,
                maximum=200_000,
            ),
            max_upload_bytes=_bounded_int(
                env,
                "MAX_UPLOAD_BYTES",
                5_000_000,
                minimum=1_024,
                maximum=25_000_000,
            ),
            max_request_body_bytes=_bounded_int(
                env,
                "MAX_REQUEST_BODY_BYTES",
                8_000_000,
                minimum=1_024,
                maximum=100_000_000,
            ),
            max_pdf_pages=_bounded_int(
                env,
                "MAX_PDF_PAGES",
                100,
                minimum=1,
                maximum=10_000,
            ),
            max_docx_entries=_bounded_int(
                env,
                "MAX_DOCX_ENTRIES",
                2_000,
                minimum=1,
                maximum=100_000,
            ),
            max_docx_uncompressed_bytes=_bounded_int(
                env,
                "MAX_DOCX_UNCOMPRESSED_BYTES",
                50_000_000,
                minimum=1_024,
                maximum=1_000_000_000,
            ),
            max_docx_compression_ratio=_bounded_int(
                env,
                "MAX_DOCX_COMPRESSION_RATIO",
                200,
                minimum=1,
                maximum=100_000,
            ),
            session_cleanup_retention_seconds=_bounded_int(
                env,
                "SESSION_CLEANUP_RETENTION_SECONDS",
                2_592_000,
                minimum=0,
                maximum=31_536_000,
            ),
            refresh_token_cleanup_retention_seconds=_bounded_int(
                env,
                "REFRESH_TOKEN_CLEANUP_RETENTION_SECONDS",
                2_592_000,
                minimum=0,
                maximum=31_536_000,
            ),
            user_quota_cv_analysis_attempts=_bounded_int(
                env,
                "USER_QUOTA_CV_ANALYSIS_ATTEMPTS",
                30,
                minimum=0,
                maximum=1_000_000,
            ),
            user_quota_cv_analysis_window_seconds=_bounded_int(
                env,
                "USER_QUOTA_CV_ANALYSIS_WINDOW_SECONDS",
                3_600,
                minimum=1,
                maximum=604_800,
            ),
            user_quota_job_match_attempts=_bounded_int(
                env,
                "USER_QUOTA_JOB_MATCH_ATTEMPTS",
                60,
                minimum=0,
                maximum=1_000_000,
            ),
            user_quota_job_match_window_seconds=_bounded_int(
                env,
                "USER_QUOTA_JOB_MATCH_WINDOW_SECONDS",
                3_600,
                minimum=1,
                maximum=604_800,
            ),
            user_quota_recommendations_attempts=_bounded_int(
                env,
                "USER_QUOTA_RECOMMENDATIONS_ATTEMPTS",
                30,
                minimum=0,
                maximum=1_000_000,
            ),
            user_quota_recommendations_window_seconds=_bounded_int(
                env,
                "USER_QUOTA_RECOMMENDATIONS_WINDOW_SECONDS",
                3_600,
                minimum=1,
                maximum=604_800,
            ),
            user_quota_remote_llm_attempts=_bounded_int(
                env,
                "USER_QUOTA_REMOTE_LLM_ATTEMPTS",
                20,
                minimum=0,
                maximum=1_000_000,
            ),
            user_quota_remote_llm_window_seconds=_bounded_int(
                env,
                "USER_QUOTA_REMOTE_LLM_WINDOW_SECONDS",
                3_600,
                minimum=1,
                maximum=604_800,
            ),
            common_password_check_enabled=_bool(
                env,
                "COMMON_PASSWORD_CHECK_ENABLED",
                default=True,
            ),
            default_recommendation_limit=default_limit,
            max_recommendation_limit=max_limit,
            default_candidate_pool_size=default_pool,
            max_candidate_pool_size=max_pool,
            api_rate_limit_requests=_bounded_int(
                env,
                "API_RATE_LIMIT_REQUESTS",
                120,
                minimum=0,
                maximum=100_000,
            ),
            api_rate_limit_window_seconds=_bounded_int(
                env,
                "API_RATE_LIMIT_WINDOW_SECONDS",
                60,
                minimum=1,
                maximum=86_400,
            ),
            login_rate_limit_attempts=_bounded_int(
                env,
                "LOGIN_RATE_LIMIT_ATTEMPTS",
                5,
                minimum=1,
                maximum=10_000,
            ),
            login_rate_limit_window_seconds=_bounded_int(
                env,
                "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
                300,
                minimum=1,
                maximum=86_400,
            ),
            registration_rate_limit_attempts=_bounded_int(
                env,
                "REGISTRATION_RATE_LIMIT_ATTEMPTS",
                5,
                minimum=1,
                maximum=10_000,
            ),
            registration_rate_limit_window_seconds=_bounded_int(
                env,
                "REGISTRATION_RATE_LIMIT_WINDOW_SECONDS",
                3600,
                minimum=1,
                maximum=604_800,
            ),
            refresh_rate_limit_attempts=_bounded_int(
                env,
                "REFRESH_RATE_LIMIT_ATTEMPTS",
                30,
                minimum=1,
                maximum=10_000,
            ),
            refresh_rate_limit_window_seconds=_bounded_int(
                env,
                "REFRESH_RATE_LIMIT_WINDOW_SECONDS",
                60,
                minimum=1,
                maximum=86_400,
            ),
            password_change_rate_limit_attempts=_bounded_int(
                env,
                "PASSWORD_CHANGE_RATE_LIMIT_ATTEMPTS",
                5,
                minimum=1,
                maximum=10_000,
            ),
            password_change_rate_limit_window_seconds=_bounded_int(
                env,
                "PASSWORD_CHANGE_RATE_LIMIT_WINDOW_SECONDS",
                300,
                minimum=1,
                maximum=86_400,
            ),
            rate_limit_max_keys=_bounded_int(
                env,
                "RATE_LIMIT_MAX_KEYS",
                20_000,
                minimum=100,
                maximum=1_000_000,
            ),
            rate_limit_backend=rate_limit_backend,
            redis_url=redis_url,
            redis_key_prefix=redis_key_prefix,
            redis_connect_timeout_seconds=_bounded_float(
                env,
                "REDIS_CONNECT_TIMEOUT_SECONDS",
                1.0,
                minimum=0.1,
                maximum=30.0,
            ),
            redis_socket_timeout_seconds=_bounded_float(
                env,
                "REDIS_SOCKET_TIMEOUT_SECONDS",
                1.0,
                minimum=0.1,
                maximum=30.0,
            ),
            redis_health_check_interval_seconds=_bounded_int(
                env,
                "REDIS_HEALTH_CHECK_INTERVAL_SECONDS",
                30,
                minimum=0,
                maximum=300,
            ),
            redis_max_connections=_bounded_int(
                env,
                "REDIS_MAX_CONNECTIONS",
                100,
                minimum=1,
                maximum=10_000,
            ),
            rate_limit_redis_failure_mode=cast(RedisFailureMode, redis_failure_mode),
            trusted_proxy_networks=trusted_proxy_networks,
            enable_hsts=enable_hsts,
            enable_api_docs=enable_api_docs,
            auto_create_tables=auto_create_tables,
            debug=debug,
            log_level=log_level,
            log_json=log_json,
        )


@lru_cache(maxsize=1)
def get_api_settings() -> APISettings:
    return APISettings.from_env()
