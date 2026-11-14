from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.settings import APIConfigurationError
from app.db.settings import DatabaseSettings


def get_database_settings(environ: Mapping[str, str] | None = None) -> DatabaseSettings:
    return DatabaseSettings.from_env(os.environ if environ is None else environ)


def get_database_url(environ: Mapping[str, str] | None = None) -> str:
    return get_database_settings(environ).url


@lru_cache(maxsize=4)
def engine_for_url(url: str) -> Engine:
    try:
        settings = get_database_settings()
    except APIConfigurationError:
        settings = DatabaseSettings(url=url)
    return create_engine(url, **settings.engine_options())


@lru_cache(maxsize=4)
def session_factory_for_url(url: str) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine_for_url(url),
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def get_engine() -> Engine:
    return engine_for_url(get_database_url())


def get_session_factory() -> sessionmaker[Session]:
    return session_factory_for_url(get_database_url())


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def dispose_engines() -> None:
    try:
        get_engine().dispose()
    except Exception:
        pass
    engine_for_url.cache_clear()
    session_factory_for_url.cache_clear()
