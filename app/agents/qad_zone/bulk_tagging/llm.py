"""LLM wrapper for the bulk-tagging pipeline (web-app variant).

In doc-gener-bulk this module did its own httpx POST + JSON mode + 429 retry.
In the web app we reuse ``app.core.llm.openai_chat`` so there's a single LLM
client, single retry policy, single env-var binding.

``openai_call`` keeps the same signature as the standalone version — accepts
a ``client`` parameter for compatibility but ignores it (the central client
manages its own httpx). Returns a parsed dict (callers expect JSON mode).
"""
from __future__ import annotations

import re

from app.core.llm import openai_chat, parse_json_response


_MODTAG_RE = re.compile(r"[A-Z0-9_]+")


def normalise_module_tag(raw: str) -> str:
    """Pull the first ASCII-uppercase token out of whatever the LLM returned.

    Defensive against the LLM polluting module_tag with extra content:
        'EINVOICE/maintenance'  → 'EINVOICE'
        'DOA / approval'        → 'DOA'
        '"INVAPPR"'             → 'INVAPPR'
        '  SHARED  '            → 'SHARED'
    """
    if not raw:
        return ""
    upper = str(raw).strip().upper()
    m = _MODTAG_RE.search(upper)
    return m.group(0) if m else ""


async def openai_call(
    client,                               # ignored — kept for back-compat
    system: str,
    user: str,
    *,
    max_tokens: int = 600,
    model: str | None = None,
) -> dict:
    """Compat shim — delegate to app.core.llm.openai_chat + parse_json_response.

    The original doc-gener-bulk version took an ``httpx.AsyncClient`` as the
    first positional arg and used JSON mode via response_format. The central
    ``openai_chat`` doesn't expose response_format, but the per-pass prompts
    are all very explicit about returning ONLY valid JSON and we run them
    with temperature=0 (default in ``openai_chat`` is 0.2 — see override
    below). ``parse_json_response`` then tolerantly extracts the JSON object.

    NOTE: ``openai_chat`` defaults to temperature=0.2; for tagging we want
    deterministic output so we pass temperature=0 explicitly.
    """
    raw = await openai_chat(
        system,
        user,
        max_tokens=max_tokens,
        model=model,
        temperature=0.0,
    )
    try:
        return parse_json_response(raw)
    except Exception:
        # Re-raise with the raw payload truncated so the orchestrator can log
        # context and continue gracefully (most callers wrap this in try/except).
        raise RuntimeError(f"LLM did not return valid JSON. First 400 chars: {raw[:400]!r}")
