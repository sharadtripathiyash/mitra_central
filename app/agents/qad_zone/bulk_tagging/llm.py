"""LLM wrapper for the bulk-tagging pipeline.

The standalone doc-gener-bulk tool built its own httpx client. In the web app
we route through ``app.core.llm.chat()`` — a single dispatcher that can call
EITHER OpenAI or Anthropic based on the model_spec string (e.g.
``"openai:gpt-5.4-mini"`` or ``"anthropic:claude-opus-4-7:thinking=8000"``).

Per-pass model picks live in ``app/agents/qad_zone/llm_models.py``. Callers
import their pass's specific model constant and pass it as ``model=`` here.

``openai_call`` (despite the legacy name) is provider-agnostic — keeps the
same call signature as the standalone version so the pass modules don't have
to change their internal API.
"""
from __future__ import annotations

import re

from app.core.llm import chat, parse_json_response


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
    model: str = "openai:gpt-5.4-mini",
) -> dict:
    """Provider-agnostic LLM call returning a parsed dict.

    Despite the legacy name, ``model`` may be EITHER an OpenAI or Anthropic
    spec — see ``app/core/llm.py::chat()`` for the format. Defaults to
    ``openai:gpt-5.4-mini`` so callers that don't pass a model still work.

    OpenAI calls auto-enable JSON mode. Anthropic calls auto-cache the
    system prompt (90% cheaper on repeat-context calls). Both honour 429 /
    5xx retry with backoff and 180s timeout (configured inside the
    central client).
    """
    raw = await chat(
        model,
        system,
        user,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    try:
        return parse_json_response(raw)
    except Exception:
        # Re-raise with the raw payload truncated so the orchestrator can log
        # context and continue gracefully (most callers wrap this in try/except).
        raise RuntimeError(
            f"LLM did not return valid JSON. Model={model!r}. "
            f"First 400 chars: {raw[:400]!r}"
        )
