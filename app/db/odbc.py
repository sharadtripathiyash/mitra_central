"""ODBC connection manager for QAD Progress DB.

Runs queries on a thread pool since pyodbc is blocking. Returns rows as
list[dict] for easy JSON serialisation to the UI.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.config import settings

logger = logging.getLogger(__name__)

try:  # pyodbc is optional at import time so the app can boot without a driver installed
    import pyodbc  # type: ignore
except Exception as exc:  # pragma: no cover
    pyodbc = None  # type: ignore
    logger.warning("pyodbc not available: %s", exc)


def _build_connection_string() -> str:
    if settings.odbc_connection_string:
        return settings.odbc_connection_string
    parts = [f"DSN={settings.odbc_dsn}"]
    if settings.odbc_user:
        parts.append(f"UID={settings.odbc_user}")
    if settings.odbc_password:
        parts.append(f"PWD={settings.odbc_password}")
    return ";".join(parts)


@contextmanager
def _connect() -> Iterator["pyodbc.Connection"]:  # type: ignore[name-defined]
    if pyodbc is None:
        raise RuntimeError("pyodbc driver is not installed in this environment")
    conn = pyodbc.connect(_build_connection_string(), autocommit=True, timeout=30)
    try:
        yield conn
    finally:
        conn.close()


def _run_query_sync(sql: str, params: tuple | None = None, limit: int = 1000) -> dict[str, Any]:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        if cur.description is None:
            return {"columns": [], "rows": [], "row_count": cur.rowcount}
        columns = [c[0] for c in cur.description]
        raw = cur.fetchmany(limit)
        rows = [dict(zip(columns, [_coerce(v) for v in row])) for row in raw]
        return {"columns": columns, "rows": rows, "row_count": len(rows)}


def _coerce(v: Any) -> Any:
    """Make values JSON-safe (dates, decimals, bytes, etc.)."""
    from datetime import date, datetime, time
    from decimal import Decimal

    if v is None:
        return None
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8", errors="replace")
        except Exception:
            return str(v)
    return v


async def run_query(sql: str, params: tuple | None = None, limit: int = 1000) -> dict[str, Any]:
    """Async wrapper — runs the blocking ODBC query on a thread."""
    return await asyncio.to_thread(_run_query_sync, sql, params, limit)


def _is_safe_select(sql: str) -> bool:
    """Very basic read-only guard. The primary safety boundary is n8n, but we
    add a belt-and-braces check here so nothing destructive ever reaches QAD."""
    if not sql:
        return False
    stripped = sql.strip().lower().lstrip("(").strip()
    if not stripped.startswith(("select", "with")):
        return False
    forbidden = (" insert ", " update ", " delete ", " drop ", " alter ", " truncate ", " merge ", " grant ", " revoke ")
    padded = f" {stripped} "
    return not any(k in padded for k in forbidden)


async def run_select(sql: str, limit: int = 1000) -> dict[str, Any]:
    if not _is_safe_select(sql):
        raise ValueError("Only read-only SELECT / WITH queries are allowed.")
    return await run_query(sql, None, limit)
