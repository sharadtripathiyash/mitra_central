"""Jira Cloud REST client — backs the Apex "create a ticket" (ITSM) escalation.

Credentials are read from settings (loaded from .env): JIRA_BASE_URL, JIRA_EMAIL,
JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_ISSUE_TYPE. The API token is a secret and
must NEVER be sent to the frontend — every call here runs server-side.
"""
from __future__ import annotations

import base64
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class JiraError(Exception):
    """Raised when Jira is not configured or issue creation fails."""


def is_configured() -> bool:
    return bool(settings.jira_base_url and settings.jira_email and settings.jira_api_token)


def _auth_header() -> str:
    raw = f"{settings.jira_email}:{settings.jira_api_token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _text_to_adf(text: str) -> dict:
    """Convert plain text (with newlines) into a minimal Atlassian Document Format
    doc. Jira REST v3 requires the `description` field as ADF, not a plain string.
    Each line becomes a paragraph; blank lines become empty paragraphs (spacing)."""
    content: list[dict] = []
    for line in (text or "").split("\n"):
        if line.strip():
            content.append({"type": "paragraph",
                            "content": [{"type": "text", "text": line}]})
        else:
            content.append({"type": "paragraph", "content": []})
    if not content:
        content = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": content}


async def create_issue(
    summary: str,
    description: str,
    *,
    labels: list[str] | None = None,
    issue_type: str | None = None,
) -> dict:
    """Create a Jira issue. Returns {"id", "key", "url"}. Raises JiraError on failure."""
    if not is_configured():
        raise JiraError("Jira is not configured (set JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN in .env).")

    base = settings.jira_base_url.rstrip("/")
    fields: dict = {
        "project": {"key": settings.jira_project_key},
        "summary": (summary or "Apex support request").strip()[:240],
        "issuetype": {"name": issue_type or settings.jira_issue_type or "Task"},
        "description": _text_to_adf(description),
    }
    if labels:
        # Jira labels cannot contain spaces.
        fields["labels"] = [l.replace(" ", "-") for l in labels if l]

    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{base}/rest/api/3/issue", json={"fields": fields}, headers=headers)

    if resp.status_code not in (200, 201):
        logger.error("Jira create failed: %s %s", resp.status_code, resp.text[:500])
        raise JiraError(f"Jira returned {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    key = data.get("key", "")
    return {"id": data.get("id", ""), "key": key, "url": f"{base}/browse/{key}"}
