from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.api.settings import APIConfigurationError, read_app_environment

AI_ENGINE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = AI_ENGINE_DIR / "data" / "jobmatch.db"


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


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    url: str
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout_seconds: int = 30
    pool_recycle_seconds: int = 1800
    connect_timeout_seconds: int = 5

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DatabaseSettings:
        env = os.environ if environ is None else environ
        app_env = read_app_environment(env)
        configured = (env.get("DATABASE_URL") or "").strip()
        if configured:
            url = configured
        else:
            if app_env == "production":
                raise APIConfigurationError(
                    "DATABASE_URL is required in production."
                )
            DEFAULT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"

        if app_env == "production" and url.startswith("sqlite"):
            raise APIConfigurationError(
                "DATABASE_URL must use PostgreSQL in production."
            )
        if not (url.startswith("sqlite") or url.startswith("postgresql")):
            raise APIConfigurationError(
                "DATABASE_URL must use SQLite or PostgreSQL."
            )

        return cls(
            url=url,
            pool_size=_bounded_int(
                env,
                "DB_POOL_SIZE",
                5,
                minimum=1,
                maximum=100,
            ),
            max_overflow=_bounded_int(
                env,
                "DB_MAX_OVERFLOW",
                10,
                minimum=0,
                maximum=100,
            ),
            pool_timeout_seconds=_bounded_int(
                env,
                "DB_POOL_TIMEOUT_SECONDS",
                30,
                minimum=1,
                maximum=300,
            ),
            pool_recycle_seconds=_bounded_int(
                env,
                "DB_POOL_RECYCLE_SECONDS",
                1800,
                minimum=60,
                maximum=86_400,
            ),
            connect_timeout_seconds=_bounded_int(
                env,
                "DB_CONNECT_TIMEOUT_SECONDS",
                5,
                minimum=1,
                maximum=60,
            ),
        )

    def engine_options(self) -> dict[str, object]:
        if self.is_sqlite:
            return {"connect_args": {"check_same_thread": False}}
        return {
            "pool_pre_ping": True,
            "pool_size": self.pool_size,
            "max_overflow": self.max_overflow,
            "pool_timeout": self.pool_timeout_seconds,
            "pool_recycle": self.pool_recycle_seconds,
            "connect_args": {"connect_timeout": self.connect_timeout_seconds},
        }
