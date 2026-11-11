"""Executes .sql files that use ``DELIMITER`` (MySQL Workbench style), which drivers cannot do natively."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from .db_manager import DatabaseConnection
from .exceptions import DatabaseError

log = logging.getLogger(__name__)


def split_statements(script: str) -> list[str]:
    """Split a SQL script into statements.

    Understands ``DELIMITER`` directives, ``--``/``#``/``/* */`` comments and quoted strings/identifiers,
    so semicolons inside stored-procedure bodies or string literals never split a statement.
    """
    statements: list[str] = []
    buf: list[str] = []
    delimiter = ";"
    i, n = 0, len(script)
    at_line_start = True

    def flush() -> None:
        text = "".join(buf).strip()
        if text:
            statements.append(text)
        buf.clear()

    while i < n:
        ch = script[i]
        # DELIMITER directive (only at the start of a line, outside any statement text)
        if at_line_start and script[i:i + 9].upper() == "DELIMITER" and not "".join(buf).strip():
            eol = script.find("\n", i)
            eol = n if eol == -1 else eol
            delimiter = script[i + 9:eol].strip() or ";"
            i = eol + 1
            at_line_start = True
            continue
        # comments
        if script.startswith("--", i) and (i + 2 >= n or script[i + 2] in " \t\r\n"):
            eol = script.find("\n", i)
            i = n if eol == -1 else eol
            continue
        if ch == "#":
            eol = script.find("\n", i)
            i = n if eol == -1 else eol
            continue
        if script.startswith("/*", i):
            end = script.find("*/", i + 2)
            end = n if end == -1 else end + 2
            buf.append(script[i:end])  # keep: may be a /*! ... */ conditional comment
            i = end
            continue
        # quoted strings / identifiers
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if script[j] == "\\" and ch != "`":
                    j += 2
                    continue
                if script[j] == ch:
                    if j + 1 < n and script[j + 1] == ch:  # doubled quote = escaped quote
                        j += 2
                        continue
                    break
                j += 1
            buf.append(script[i:j + 1])
            i = j + 1
            at_line_start = False
            continue
        # statement terminator
        if script.startswith(delimiter, i):
            flush()
            i += len(delimiter)
            at_line_start = False
            continue
        buf.append(ch)
        at_line_start = ch == "\n" or (at_line_start and ch in " \t\r")
        i += 1
    flush()
    return statements


class SqlScriptRunner:
    """Runs scripts against the shared pool, remapping schema names to the configured ones."""

    def __init__(self, db: Optional[DatabaseConnection] = None) -> None:
        self._db = db or DatabaseConnection()

    def _remap(self, sql: str) -> str:
        for default, actual in self._db.settings.schema_map.items():
            if default != actual:
                sql = re.sub(rf"\b{re.escape(default)}\b", actual, sql)
        return sql

    def run_script(self, script: str, label: str = "script") -> int:
        statements = split_statements(self._remap(script))
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                for number, stmt in enumerate(statements, start=1):
                    try:
                        cur.execute(stmt)
                        while cur.nextset():
                            pass
                    except Exception as exc:
                        preview = " ".join(stmt.split())[:140]
                        raise DatabaseError(f"{label}: statement #{number} failed: {exc} | {preview}") from exc
            conn.commit()
        log.info("Executed %s (%d statements)", label, len(statements))
        return len(statements)

    def run_file(self, path: Path) -> int:
        return self.run_script(Path(path).read_text(encoding="utf-8"), label=Path(path).name)
