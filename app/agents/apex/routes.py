"""HTTP + WebSocket routes for the Apex floating RAG widget."""
from __future__ import annotations

import base64
import json
import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents.apex.service import handle_apex_ws
from app.core.session import get_context, set_context
from app.core.config import settings
from app.integrations.jira_client import JiraError, create_issue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/apex", tags=["apex"])


def _parse_ws_user(ws: WebSocket) -> dict | None:
    from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
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


@router.websocket("/ws")
async def apex_ws(ws: WebSocket):
    user = _parse_ws_user(ws)
    if not user:
        await ws.accept()
        await ws.close(code=4001, reason="unauthenticated")
        return
    await ws.accept()
    try:
        await handle_apex_ws(ws, user["session_id"], user)
    except WebSocketDisconnect:
        pass


@router.get("/context")
async def apex_context_get(request: Request):
    """Return the current saved domains for the session."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    ctx = get_context(user["session_id"], "apex") or {}
    return JSONResponse({"domains": ctx.get("domains", [])})


class ContextUpdate(BaseModel):
    domains: list[str]


@router.post("/context")
async def apex_context_post(request: Request, body: ContextUpdate):
    """Update the active domains for the session (in-session module switch)."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    # Normalise domain names
    mapping = {
        "purchase": "purchasing", "purchases": "purchasing",
        "sale": "sales",
        "mfg": "manufacturing",
    }
    normalised = [mapping.get(d.strip().lower(), d.strip().lower()) for d in body.domains]
    set_context(user["session_id"], "apex", {"domains": normalised})
    return JSONResponse({"ok": True, "domains": normalised})


class TicketCreate(BaseModel):
    summary: str
    description: str = ""
    area: list[str] | None = None


@router.post("/ticket")
async def apex_create_ticket(request: Request, body: TicketCreate):
    """Create a Jira ticket from an Apex conversation (ITSM escalation)."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    summary = (body.summary or "").strip()
    if not summary:
        return JSONResponse({"ok": False, "error": "A summary is required."}, status_code=400)

    username = user.get("username", "unknown")
    roles = ", ".join(user.get("roles", []) or []) or "—"
    areas = [a for a in (body.area or []) if isinstance(a, str)]
    area_str = ", ".join(areas) or "—"

    description = (
        f"{body.description.strip()}\n\n"
        f"---\n"
        f"Raised via Apex Assistant\n"
        f"User: {username}  |  Roles: {roles}\n"
        f"Area / module: {area_str}"
    )
    labels = ["apex-assistant"] + [f"area-{a}" for a in areas]

    try:
        result = await create_issue(summary, description, labels=labels)
    except JiraError as exc:
        logger.warning("Jira ticket creation failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error creating Jira ticket")
        return JSONResponse({"ok": False, "error": f"Unexpected error: {exc}"}, status_code=500)

    return JSONResponse({"ok": True, "key": result["key"], "url": result["url"]})
