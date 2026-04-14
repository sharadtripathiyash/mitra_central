"""HTTP + WebSocket routes for the Mitra text-to-SQL agent."""
from __future__ import annotations

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.agents.mitra.service import handle_mitra_ws
from app.agents.registry import sidebar_agents
from app.core.config import settings
from app.core.session import get_user_settings, set_user_settings

router = APIRouter(prefix="/agents/mitra", tags=["mitra"])
templates = Jinja2Templates(directory="app/templates")

SAMPLE_QUESTIONS = {
    "sales": [
        "Show top 10 customers by order count",
        "What is the current sales backlog?",
        "Open sales orders due this week",
    ],
    "purchase": [
        "Show open purchase orders by supplier",
        "Total purchase value for last month",
        "List late deliveries in the last 15 days",
    ],
    "manufacturing": [
        "Show work in progress by work center",
        "Component shortages on open work orders",
        "Production completed yesterday",
    ],
    "inventory": [
        "What items have low inventory?",
        "Show items below reorder point",
        "What should I order?",
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
    return [q for q in out if not (q in seen or seen.add(q))][:8]


def _parse_ws_user(ws: WebSocket) -> dict | None:
    """Extract user from signed session cookie on WebSocket.

    Starlette's SessionMiddleware encodes sessions as:
        TimestampSigner.sign(base64(json_bytes))
    so we must use TimestampSigner (not URLSafeTimedSerializer) to unsign,
    then base64-decode and JSON-parse the payload.
    """
    import base64
    import json
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


@router.get("", response_class=HTMLResponse)
async def mitra_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not user.get("roles"):
        return RedirectResponse("/roles", status_code=303)
    user_settings = get_user_settings(user["session_id"])
    active_domain = user_settings.get("qad_domain") or settings.qad_domain
    return templates.TemplateResponse("agents/mitra.html", {
        "request": request,
        "user": user,
        "agents": sidebar_agents(),
        "active": "mitra",
        "agent": {"key": "mitra", "name": "Apex", "icon": "message-square",
                  "description": "Natural‑Language Intelligence for QAD Data.",
                  "route_prefix": "/agents/mitra"},
        "suggestions": _get_suggestions(user.get("roles", [])),
        "active_domain": active_domain,
        "default_domain": settings.qad_domain,
    })


@router.websocket("/ws")
async def mitra_ws(ws: WebSocket):
    user = _parse_ws_user(ws)
    if not user:
        await ws.accept()
        await ws.close(code=4001, reason="unauthenticated")
        return
    await ws.accept()
    try:
        await handle_mitra_ws(ws, user["session_id"], user)
    except WebSocketDisconnect:
        pass


# ── Domain endpoints ──────────────────────────────────────────────────────────

class DomainUpdate(BaseModel):
    domain: str


@router.get("/domain")
async def mitra_domain_get(request: Request):
    """Return the active QAD domain for this session."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    user_settings = get_user_settings(user["session_id"])
    domain = user_settings.get("qad_domain") or settings.qad_domain
    return JSONResponse({"domain": domain, "default": settings.qad_domain})


@router.post("/domain")
async def mitra_domain_post(request: Request, body: DomainUpdate):
    """Persist the chosen QAD domain for this session."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    domain = body.domain.strip().upper()
    if not domain:
        return JSONResponse({"error": "domain required"}, status_code=400)
    set_user_settings(user["session_id"], {"qad_domain": domain})
    return JSONResponse({"ok": True, "domain": domain})