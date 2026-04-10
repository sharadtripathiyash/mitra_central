"""In-memory per-agent conversation session store.

No external dependencies (no Redis). Uses cachetools TTLCache so entries
expire automatically. If the app restarts, sessions reset.

Each (user_session_id, agent_key) pair gets its own history list and
optional context dict.
"""
from __future__ import annotations

import threading
from typing import Any

from cachetools import TTLCache

from app.core.config import settings

_lock = threading.Lock()

# Max 2000 session keys, each lives for session_ttl_seconds
_store: TTLCache = TTLCache(maxsize=2000, ttl=settings.session_ttl_seconds)


def _key(session_id: str, agent_key: str, suffix: str = "hist") -> str:
    return f"{agent_key}:{session_id}:{suffix}"


def append_turn(session_id: str, agent_key: str, turn: dict) -> None:
    k = _key(session_id, agent_key, "hist")
    with _lock:
        hist: list = _store.get(k, [])
        hist.append(turn)
        if len(hist) > 30:
            hist = hist[-30:]
        _store[k] = hist


def load_history(session_id: str, agent_key: str) -> list[dict]:
    k = _key(session_id, agent_key, "hist")
    with _lock:
        return list(_store.get(k, []))


def set_context(session_id: str, agent_key: str, ctx: dict) -> None:
    k = _key(session_id, agent_key, "ctx")
    with _lock:
        _store[k] = ctx


def get_context(session_id: str, agent_key: str) -> dict[str, Any] | None:
    k = _key(session_id, agent_key, "ctx")
    with _lock:
        return _store.get(k)


def get_user_settings(session_id: str) -> dict:
    k = f"settings:{session_id}"
    with _lock:
        return _store.get(k, {"row_limit": settings.default_row_limit})


def set_user_settings(session_id: str, data: dict) -> None:
    k = f"settings:{session_id}"
    with _lock:
        current = _store.get(k, {"row_limit": settings.default_row_limit})
        current.update(data)
        _store[k] = current
