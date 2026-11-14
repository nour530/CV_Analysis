from __future__ import annotations

from app.db.base import Base
from app.db.session import get_engine


def init_database() -> None:
    import app.db.models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())
