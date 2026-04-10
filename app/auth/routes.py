"""Authentication routes — login, role selection, logout, settings."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.security import new_session_id, verify_credentials
from app.core.session import get_user_settings, set_user_settings
from app.agents.registry import sidebar_agents

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

VALID_ROLES = {"sales", "purchase", "manufacturing", "other"}


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    if not verify_credentials(username, password):
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=401,
        )
    request.session["user"] = {
        "username": username,
        "session_id": new_session_id(),
        "roles": [],
    }
    return RedirectResponse("/roles", status_code=303)


@router.get("/roles", response_class=HTMLResponse)
async def roles_get(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("auth/roles.html", {"request": request, "user": user})


@router.post("/roles", response_class=HTMLResponse)
async def roles_post(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    selected = [r for r in form.getlist("roles") if r in VALID_ROLES]
    if not selected:
        return templates.TemplateResponse(
            "auth/roles.html",
            {"request": request, "user": user, "error": "Please select at least one role"},
            status_code=400,
        )
    user["roles"] = selected
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    user_settings = get_user_settings(user["session_id"])
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "agents": sidebar_agents(),
        "active": "settings",
        "row_limit": user_settings.get("row_limit", 50),
        "success": False,
    })


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request, row_limit: int = Form(50)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    row_limit = max(1, min(500, row_limit))
    set_user_settings(user["session_id"], {"row_limit": row_limit})
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "agents": sidebar_agents(),
        "active": "settings",
        "row_limit": row_limit,
        "success": True,
    })