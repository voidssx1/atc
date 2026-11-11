from typing import Any

from app.db.base import Base
from app.db.session import get_db
import app.models  # noqa: F401 - ensure all models are imported

__all__ = ["Base", "engine", "SessionLocal", "get_db"]


def __getattr__(name: str) -> Any:
    """Lazily forward `engine` and `SessionLocal` to `app.db.session`."""
    if name in ("engine", "SessionLocal"):
        from app.db import session as _db_session

        return getattr(_db_session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
