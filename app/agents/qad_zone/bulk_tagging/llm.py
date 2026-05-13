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

    Matches the standalone doc-gener-bulk LLM contract exactly:
      • temperature=0        (deterministic tagging)
      • JSON mode ON         (response_format={"type":"json_object"}) — forces
                              the model to commit to strict JSON. Critical
                              for Pass 4.5 (merges) and Pass A (glossary)
                              where the model otherwise hedges with prose.
      • 429 + 5xx retry      (inherited from openai_chat — 5 attempts, honours
                              Retry-After)
      • 180s timeout         (inherited — gives big Pass 3 / Pass 4.5 calls
                              enough room)
    """
    raw = await openai_chat(
        system,
        user,
        max_tokens=max_tokens,
        model=model,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    try:
        return parse_json_response(raw)
    except Exception:
        # Re-raise with the raw payload truncated so the orchestrator can log
        # context and continue gracefully (most callers wrap this in try/except).
        raise RuntimeError(f"LLM did not return valid JSON. First 400 chars: {raw[:400]!r}")
