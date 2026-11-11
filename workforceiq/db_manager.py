"""MySQL connection management.

``DatabaseConnection`` is a **Singleton**: exactly one instance -- and therefore exactly one thread-safe
connection pool -- exists per process, no matter how many times it is constructed. The Data Access Layer
manager classes *inherit* from it (as required by the assignment) but delegate to the same shared pool, so
a Streamlit app with many managers still opens at most ``pool_size`` connections.
"""
from __future__ import annotations

import logging
import queue
import re
import ssl
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

import pandas as pd
import pymysql
from pymysql.constants import FIELD_TYPE
from pymysql.converters import conversions
from pymysql.cursors import DictCursor

from .config import Settings
from .exceptions import (
    DatabaseConnectionError,
    DatabaseError,
    DuplicateError,
    ValidationError,
)

log = logging.getLogger(__name__)

# MySQL error codes we translate into friendly application errors
_DUP_ENTRY = 1062
_NOT_NULL = 1048
_FK_CHILD, _FK_PARENT = 1452, 1451
_CHECK_FAILED = 3819
_SIGNAL = 1644  # raised by SIGNAL SQLSTATE '45000' in our stored procedures
_DATA_TOO_LONG, _OUT_OF_RANGE, _TRUNCATED = 1406, 1264, 1265
_CONNECTION_CODES = {2002, 2003, 2006, 2013, 1045, 1044, 1049}

_PROC_NAME = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


def translate_error(exc: pymysql.MySQLError) -> Exception:
    """Convert a raw driver error into an application exception with a user-safe message."""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    text = str(exc.args[1]) if len(exc.args) > 1 else str(exc)
    if code == _DUP_ENTRY:
        return DuplicateError("A record with the same unique value already exists.")
    if code == _SIGNAL:
        return ValidationError(text)
    if code == _CHECK_FAILED:
        return ValidationError("A value is outside its allowed range (database check constraint).")
    if code in (_FK_CHILD, _FK_PARENT):
        return ValidationError("The record references (or is referenced by) a record that does not match.")
    if code == _NOT_NULL:
        return ValidationError("A required value is missing.")
    if code in (_DATA_TOO_LONG, _OUT_OF_RANGE, _TRUNCATED):
        return ValidationError("A value is too long or out of range for its column.")
    if code in _CONNECTION_CODES or isinstance(exc, pymysql.err.OperationalError) and code in (None, 2003):
        return DatabaseConnectionError(
            "Cannot connect to the MySQL server. Check host/port/credentials and that the schemas exist."
        )
    return DatabaseError(f"Database error ({code}): {text}")


class _ConnectionPool:
    """Small thread-safe LIFO pool of PyMySQL connections (validated with ping on checkout)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._idle: "queue.LifoQueue[pymysql.connections.Connection]" = queue.LifoQueue(settings.pool_size)
        self._created = 0
        self._lock = threading.Lock()

    def _new_connection(self) -> pymysql.connections.Connection:
        s = self._settings
        conv = conversions.copy()
        conv[FIELD_TYPE.DECIMAL] = float
        conv[FIELD_TYPE.NEWDECIMAL] = float
        ssl_arg: Any = None
        if s.use_ssl:
            ssl_arg = {"ca": s.ssl_ca} if s.ssl_ca else ssl.create_default_context()
        return pymysql.connect(
            host=s.host, port=s.port, user=s.user, password=s.password,
            charset="utf8mb4", cursorclass=DictCursor, autocommit=False,
            connect_timeout=s.connect_timeout, conv=conv, ssl=ssl_arg,
        )

    def acquire(self, timeout: float = 30.0) -> pymysql.connections.Connection:
        try:
            conn = self._idle.get_nowait()
        except queue.Empty:
            with self._lock:
                can_create = self._created < self._settings.pool_size
                if can_create:
                    self._created += 1
            if can_create:
                try:
                    return self._new_connection()
                except Exception:
                    with self._lock:
                        self._created -= 1
                    raise
            try:
                conn = self._idle.get(timeout=timeout)
            except queue.Empty as exc:
                raise DatabaseError("All database connections are busy; try again shortly.") from exc
        try:
            conn.ping(reconnect=True)
        except pymysql.MySQLError:
            self._discard(conn)
            with self._lock:
                self._created += 1
            return self._new_connection()
        return conn

    def release(self, conn: pymysql.connections.Connection) -> None:
        try:
            if conn.open:
                conn.rollback()  # never hand back a connection with an open transaction
                self._idle.put_nowait(conn)
                return
        except (pymysql.MySQLError, queue.Full):
            pass
        self._discard(conn)

    def _discard(self, conn: pymysql.connections.Connection) -> None:
        try:
            conn.close()
        except pymysql.MySQLError:
            pass
        with self._lock:
            self._created = max(0, self._created - 1)

    def close_all(self) -> None:
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                break
            self._discard(conn)


class DatabaseConnection:
    """Singleton gateway to MySQL. Construct freely -- you always share one pool."""

    _instance: Optional["DatabaseConnection"] = None
    _instance_lock = threading.RLock()
    _pool: Optional[_ConnectionPool] = None
    _settings: Optional[Settings] = None

    def __new__(cls, *args: Any, **kwargs: Any):
        if cls is not DatabaseConnection:
            # DAL managers inherit the behaviour but are ordinary objects sharing the singleton's pool.
            return super().__new__(cls)
        with DatabaseConnection._instance_lock:
            if DatabaseConnection._instance is None:
                DatabaseConnection._instance = super().__new__(cls)
            return DatabaseConnection._instance

    def __init__(self, settings: Optional[Settings] = None) -> None:
        if settings is not None:
            with DatabaseConnection._instance_lock:
                if DatabaseConnection._pool is not None and settings != DatabaseConnection._settings:
                    raise DatabaseError("DatabaseConnection already configured; call reset() first.")
                DatabaseConnection._settings = settings

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def reset(cls) -> None:
        """Close all pooled connections and forget settings (used by tests / reconfiguration)."""
        with DatabaseConnection._instance_lock:
            if DatabaseConnection._pool is not None:
                DatabaseConnection._pool.close_all()
            DatabaseConnection._pool = None
            DatabaseConnection._settings = None

    @property
    def settings(self) -> Settings:
        with DatabaseConnection._instance_lock:
            if DatabaseConnection._settings is None:
                DatabaseConnection._settings = Settings.from_env()
            return DatabaseConnection._settings

    def _get_pool(self) -> _ConnectionPool:
        with DatabaseConnection._instance_lock:
            if DatabaseConnection._pool is None:
                self.settings.require_credentials()
                DatabaseConnection._pool = _ConnectionPool(self.settings)
            return DatabaseConnection._pool

    # ---------------------------------------------------------------- primitives
    @contextmanager
    def connection(self) -> Iterator[pymysql.connections.Connection]:
        pool = self._get_pool()
        try:
            conn = pool.acquire()
        except pymysql.MySQLError as exc:
            raise translate_error(exc) from exc
        try:
            yield conn
        except pymysql.MySQLError as exc:
            conn.rollback()
            raise translate_error(exc) from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.release(conn)

    @contextmanager
    def transaction(self) -> Iterator[pymysql.cursors.Cursor]:
        """Cursor inside a transaction: commit on success, rollback on any error."""
        with self.connection() as conn:
            with conn.cursor() as cur:
                yield cur
            conn.commit()

    def fetch_all(self, sql: str, params: Optional[Sequence[Any]] = None) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
            conn.commit()
            return rows

    def fetch_one(self, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[dict[str, Any]]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def fetch_df(self, sql: str, params: Optional[Sequence[Any]] = None) -> pd.DataFrame:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
            columns = [c[0] for c in (cur.description or [])]
            conn.commit()
        return pd.DataFrame(rows, columns=columns)

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> int:
        with self.transaction() as cur:
            return cur.execute(sql, params)

    def execute_many(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        if not rows:
            return 0
        with self.transaction() as cur:
            return cur.executemany(sql, rows)

    def call_procedure(self, name: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """CALL schema.proc(args...). Returns the first result set (if any); commits on success."""
        if not _PROC_NAME.match(name):
            raise ValidationError(f"Invalid procedure name: {name!r}")
        placeholders = ", ".join(["%s"] * len(args))
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(f"CALL {name}({placeholders})", tuple(args))
            rows = list(cur.fetchall())
            while cur.nextset():  # drain remaining result sets so the connection is reusable
                pass
            conn.commit()
            return rows

    def healthcheck(self) -> tuple[bool, str]:
        try:
            row = self.fetch_one("SELECT VERSION() AS v")
            return True, f"MySQL {row['v']}" if row else "connected"
        except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised
            return False, str(exc)
