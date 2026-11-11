"""Base class of the Data Access Layer: inherits DatabaseConnection (shared Singleton pool)."""
from __future__ import annotations

import functools
import logging

from ..db_manager import DatabaseConnection
from ..exceptions import DatabaseError, WorkforceIQError

log = logging.getLogger(__name__)


def guarded(func):
    """try/except wrapper: application errors pass through; anything else becomes DatabaseError."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except WorkforceIQError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("%s failed", func.__name__)
            raise DatabaseError(f"{func.__name__} failed: {exc}") from exc
    return wrapper


class BaseManager(DatabaseConnection):
    @property
    def stg(self) -> str:
        return self.settings.staging_schema

    @property
    def oltp(self) -> str:
        return self.settings.oltp_schema

    @property
    def dw(self) -> str:
        return self.settings.dw_schema
