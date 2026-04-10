"""WebSocket streaming helpers.

All agents use WebSocket for real-time token-by-token delivery. This module
provides a thin protocol so the frontend knows what kind of frame it is
receiving.

Frame protocol (JSON per message)::

    {"type": "token",   "data": "some text"}       # streaming text chunk
    {"type": "status",  "data": "Searching..."}     # status indicator
    {"type": "sql",     "data": "SELECT ..."}       # generated SQL
    {"type": "table",   "data": {"columns": [...], "rows": [...], "row_count": N}}
    {"type": "chart",   "data": {chart_spec}}       # Visual Intelligence
    {"type": "sources", "data": [{...}, ...]}       # RAG sources
    {"type": "followup","data": ["q1", "q2"]}       # follow-up suggestions
    {"type": "doc",     "data": {"url": "/download/...", "title": "..."}}
    {"type": "error",   "data": "message"}
    {"type": "done",    "data": null}               # stream finished
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


async def send_frame(ws: WebSocket, frame_type: str, data: Any = None) -> None:
    try:
        await ws.send_text(json.dumps({"type": frame_type, "data": data}, default=str))
    except Exception:
        logger.debug("WS send failed (client likely disconnected)")


async def send_token(ws: WebSocket, text: str) -> None:
    await send_frame(ws, "token", text)


async def send_status(ws: WebSocket, msg: str) -> None:
    await send_frame(ws, "status", msg)


async def send_error(ws: WebSocket, msg: str) -> None:
    await send_frame(ws, "error", msg)


async def send_done(ws: WebSocket) -> None:
    await send_frame(ws, "done", None)
