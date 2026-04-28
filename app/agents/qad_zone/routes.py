"""HTTP + WebSocket routes for QAD-Zone."""
from __future__ import annotations

import base64
import json
import logging
import os
import re

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
from pydantic import BaseModel

from app.agents.qad_zone.service import handle_qadzone_ws
from app.agents.qad_zone.embedder import embed_document
from app.agents.registry import sidebar_agents
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents/qadzone", tags=["qadzone"])
templates = Jinja2Templates(directory="app/templates")


def _parse_ws_user(ws: WebSocket) -> dict | None:
    """Extract user from signed session cookie on WebSocket."""
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
async def qadzone_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not user.get("roles"):
        return RedirectResponse("/roles", status_code=303)
    return templates.TemplateResponse("agents/qadzone.html", {
        "request": request,
        "user": user,
        "agents": sidebar_agents(),
        "active": "qadzone",
        "agent": {
            "key": "qadzone", "name": "Modernization", "icon": "wrench",
            "description": "Custom code knowledge base, documentation & modernisation.",
            "route_prefix": "/agents/qadzone",
        },
    })


@router.websocket("/ws")
async def qadzone_ws(ws: WebSocket):
    user = _parse_ws_user(ws)
    if not user:
        await ws.accept()
        await ws.close(code=4001, reason="unauthenticated")
        return
    await ws.accept()
    try:
        await handle_qadzone_ws(ws, user["session_id"], user)
    except WebSocketDisconnect:
        pass


# ── Embed endpoint ────────────────────────────────────────────────────────────

class EmbedRequest(BaseModel):
    doc_url: str
    title:   str


@router.post("/embed")
async def qadzone_embed(request: Request, body: EmbedRequest):
    """Embed a generated Word doc into the Qdrant qad_custom_docs collection."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    try:
        result = await embed_document(body.doc_url, body.title)
        logger.info("Embedded doc '%s': %d chunks", body.title, result["chunks_embedded"])
        return JSONResponse({
            "ok": True,
            "chunks_embedded": result["chunks_embedded"],
            "module": result["module"],
        })
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.exception("Embed failed for '%s'", body.doc_url)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Demo endpoints ────────────────────────────────────────────────────────────

@router.get("/demo-doc/{name}")
async def demo_doc(name: str, request: Request):
    """Serve a pre-stored demo documentation file from app/static/downloads/."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    # Sanitise name — allow only alphanumeric and underscores
    safe_name = re.sub(r"[^A-Z0-9_]", "", name.upper())
    if not safe_name:
        return JSONResponse({"error": "invalid name"}, status_code=400)

    doc_path = os.path.join("app", "static", "downloads", f"{safe_name}_System_Documentation.docx")
    if not os.path.exists(doc_path):
        return JSONResponse({"error": "Document not found yet"}, status_code=404)

    filename = f"{safe_name}_System_Documentation.docx"
    return FileResponse(
        doc_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@router.post("/demo-embed")
async def demo_embed(request: Request):
    """Fake embed for demo — returns success immediately, frontend shows 3s spinner."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    return JSONResponse({"ok": True})


@router.get("/demo-blueprint/{name}")
async def demo_blueprint(name: str, request: Request):
    """Serve a pre-built migration blueprint .docx (companion to demo-doc).

    Currently only MRN ships a blueprint; DOA/RTDC return 404 until their
    blueprints are authored. The frontend hides the second download card
    when no blueprint exists for the demo module.
    """
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    safe_name = re.sub(r"[^A-Z0-9_]", "", name.upper())
    if not safe_name:
        return JSONResponse({"error": "invalid name"}, status_code=400)

    doc_path = os.path.join("app", "static", "downloads", f"{safe_name}_Migration_Blueprint.docx")
    if not os.path.exists(doc_path):
        return JSONResponse({"error": "Migration blueprint not available for this module yet"}, status_code=404)

    filename = f"{safe_name}_Migration_Blueprint.docx"
    return FileResponse(
        doc_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )
