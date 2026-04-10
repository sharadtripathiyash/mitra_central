"""Auth helpers. Phase 1 uses hardcoded credentials from settings."""
from __future__ import annotations

import hmac
import secrets
from typing import Optional

from fastapi import Request

from app.core.config import settings


def verify_credentials(username: str, password: str) -> bool:
    """Constant-time comparison against hardcoded admin/mfgpro."""
    u_ok = hmac.compare_digest(username or "", settings.auth_username)
    p_ok = hmac.compare_digest(password or "", settings.auth_password)
    return u_ok and p_ok


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def get_current_user(request: Request) -> Optional[dict]:
    """Pulls user info from the signed session cookie (set by SessionMiddleware)."""
    user = request.session.get("user")
    return user if user else None


def require_user(request: Request) -> Optional[dict]:
    """Returns user dict or None. Routes should redirect on None."""
    return get_current_user(request)
