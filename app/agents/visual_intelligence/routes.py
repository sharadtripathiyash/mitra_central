"""HTTP + WebSocket routes for Visual Intelligence."""
from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

from app.agents.visual_intelligence.service import handle_visual_ws
from app.agents.registry import sidebar_agents
from app.core.config import settings

router = APIRouter(prefix="/agents/visual", tags=["visual"])
templates = Jinja2Templates(directory="app/templates")

SAMPLE_QUESTIONS = {
    "sales": [
        "Show monthly revenue trend for the last 12 months",
        "Top 5 customers by order count",
        "Sales order status distribution",
    ],
    "purchase": [
        "Purchase value by supplier last quarter",
        "Monthly PO count trend for this year",
        "Top 10 items by purchase spend",
    ],
    "manufacturing": [
        "Daily production output last 30 days",
        "WIP quantity by item",
        "Work order status distribution",
    ],
}


def _get_suggestions(roles: list[str]) -> list[str]:
    out = []
    for r in roles:
        out.extend(SAMPLE_QUESTIONS.get(r, []))
    if not out:
        for qs in SAMPLE_QUESTIONS.values():
            out.extend(qs)
    seen = set()
    return [q for q in out if not (q in seen or seen.add(q))][:6]


def _parse_ws_user(ws: WebSocket) -> dict | None:
    """Extract user from signed session cookie on WebSocket.

    Starlette's SessionMiddleware encodes sessions as:
        TimestampSigner.sign(base64(json_bytes))
    so we must use TimestampSigner (not URLSafeTimedSerializer) to unsign,
    then base64-decode and JSON-parse the payload.
    """
    cookie = ws.cookies.get(settings.session_cookie_name)
    if not cookie:
        return None
    try:
        signer = TimestampSigner(settings.app_secret_key)
        data = signer.unsign(cookie, max_age=settings.session_ttl_seconds, return_timestamp=False)
        session_data = json.loads(base64.b64decode(data))
        return session_data.get("user")
    except (BadSignature, SignatureExpired):
        return None
    except Exception:
        return None


@router.get("", response_class=HTMLResponse)
async def visual_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not user.get("roles"):
        return RedirectResponse("/roles", status_code=303)
    return templates.TemplateResponse("agents/visual.html", {
        "request": request,
        "user": user,
        "agents": sidebar_agents(),
        "active": "visual",
        "agent": {"key": "visual", "name": "Visual Intelligence", "icon": "bar-chart-3",
                  "description": "KPIs, charts and analytics from live QAD data.",
                  "route_prefix": "/agents/visual"},
        "suggestions": _get_suggestions(user.get("roles", [])),
    })


@router.websocket("/ws")
async def visual_ws(ws: WebSocket):
    user = _parse_ws_user(ws)
    if not user:
        await ws.accept()
        await ws.close(code=4001, reason="unauthenticated")
        return
    await ws.accept()
    try:
        await handle_visual_ws(ws, user["session_id"], user)
    except WebSocketDisconnect:
        pass