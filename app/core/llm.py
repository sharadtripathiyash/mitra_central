"""Unified LLM client — routes to Groq (free/fast) or OpenAI (quality).

Usage::

    from app.core.llm import groq_chat, openai_chat, openai_stream, openai_embed, openai_search

    # Fast classification via Groq
    tables = await groq_chat(system_prompt, user_msg)

    # Quality SQL generation via OpenAI
    result = await openai_chat(system_prompt, user_msg, history=[...])

    # Streaming answer via OpenAI (yields str chunks)
    async for chunk in openai_stream(system_prompt, user_msg, history=[...]):
        await ws.send_text(chunk)

    # Embedding
    vector = await openai_embed("some text")
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


def _build_messages(
    system: str,
    user_msg: str,
    history: list[dict] | None = None,
) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": system}]
    for h in history or []:
        role = h.get("role", "user")
        content = h.get("content") or h.get("text") or h.get("q", "")
        if content:
            msgs.append({"role": role, "content": str(content)})
    msgs.append({"role": "user", "content": user_msg})
    return msgs


async def groq_chat(
    system: str,
    user_msg: str,
    *,
    history: list[dict] | None = None,
    temperature: float = 0,
    max_tokens: int = 2048,
) -> str:
    """Fast, free LLM call via Groq for classification / routing tasks."""
    payload = {
        "model": settings.groq_model,
        "messages": _build_messages(system, user_msg, history),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_GROQ_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _is_openai_reasoning_model(model: str) -> bool:
    """Detect models that lock ``temperature`` at 1 (reasoning models).

    The full GPT-5 family (gpt-5, gpt-5.2, gpt-5.3, gpt-5.4, gpt-5.5) and
    the o-series (o1, o3, o4) only accept ``temperature=1`` (the default) —
    setting any other value raises a 400 ``unsupported_value`` error.

    The mini/nano variants of those (gpt-5.4-mini, etc.) are NOT reasoning
    models; they accept arbitrary temperature.

    When we detect a reasoning model we OMIT the temperature parameter
    entirely so the model uses its default (1). This avoids the error
    without forcing a specific value.
    """
    m = (model or "").lower().strip()
    if not m:
        return False
    # Mini / nano variants are not reasoning — they accept temperature.
    if "-mini" in m or "-nano" in m:
        return False
    # gpt-5.x (full reasoning) — including future point releases
    if m.startswith("gpt-5"):
        return True
    # o-series reasoning models
    if m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return True
    return False


async def openai_chat(
    system: str,
    user_msg: str,
    *,
    history: list[dict] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    model: str | None = None,
    response_format: dict | None = None,
    timeout: float = 180.0,
    max_retries: int = 5,
) -> str:
    """Quality LLM call via OpenAI for SQL gen, RAG answers, doc gen.

    Parameters
    ----------
    response_format
        Optional pass-through to OpenAI's structured-output flag. The most
        useful value is ``{"type": "json_object"}`` which forces strict JSON
        output (the model can no longer hedge with prose / markdown fences /
        partial JSON). All bulk-tagging callers should set this.
    timeout
        Per-attempt HTTP timeout. Defaults to 180s — long enough for the
        heaviest Pass 3 / Pass 4.5 / Pass 2 documentation calls.
    max_retries
        Total attempts (not just retries) on 429 rate-limit and 5xx server
        errors. 429 honours the ``Retry-After`` header; 5xx uses exponential
        backoff. 4xx (auth, bad request) raise immediately — no point retrying.

    Backward-compatible with the prior signature: existing callers that don't
    pass ``response_format`` / ``timeout`` / ``max_retries`` behave identically
    to before, just with retry-on-failure added (strictly more robust).
    """
    resolved_model = model or settings.openai_model
    payload: dict[str, Any] = {
        "model": resolved_model,
        "messages": _build_messages(system, user_msg, history),
        # GPT-5 family (gpt-5, gpt-5.x, gpt-5.x-mini, o-series) deprecated
        # `max_tokens` — they require `max_completion_tokens`. The newer name
        # is accepted by every current OpenAI chat model, so we use it
        # universally. (`max_tokens` still works on Groq, which is why
        # groq_chat doesn't share this code path.)
        "max_completion_tokens": max_tokens,
    }
    # Reasoning models (full gpt-5.x, o-series) lock temperature at 1 and
    # reject any other value with a 400. We omit the parameter entirely so
    # they use their default. The mini/nano variants accept temperature
    # normally — pass it through.
    if not _is_openai_reasoning_model(resolved_model):
        payload["temperature"] = temperature
    elif temperature != 1.0:
        logger.debug(
            "openai_chat: omitting temperature %s for reasoning model %s "
            "(only default 1 is supported by the API)",
            temperature, resolved_model,
        )
    if response_format is not None:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_OPENAI_URL, json=payload, headers=headers)

            # ── 429: honour Retry-After, exponential backoff fallback ────────
            if resp.status_code == 429:
                ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
                if ra and ra.replace(".", "").isdigit():
                    wait = float(ra) + 1.0
                else:
                    wait = 20.0 * (attempt + 1)
                logger.warning(
                    "OpenAI 429 rate-limited (attempt %d/%d); waiting %.0fs",
                    attempt + 1, max_retries, wait,
                )
                await asyncio.sleep(min(wait, 120.0))
                last_err = RuntimeError(f"429 (attempt {attempt + 1})")
                continue

            # ── 5xx: backoff and retry ───────────────────────────────────────
            if 500 <= resp.status_code < 600:
                wait = float(2 ** attempt)
                logger.warning(
                    "OpenAI %d (attempt %d/%d); retrying in %.0fs",
                    resp.status_code, attempt + 1, max_retries, wait,
                )
                last_err = RuntimeError(f"{resp.status_code} (attempt {attempt + 1})")
                await asyncio.sleep(wait)
                continue

            # ── 4xx (other) or 2xx: process normally ─────────────────────────
            # On 4xx, surface the response body so the caller sees the actual
            # OpenAI error message (e.g. "unrecognized argument: max_tokens"),
            # not just "400 Bad Request".
            if 400 <= resp.status_code < 500:
                body = resp.text[:500] if resp.text else "(empty body)"
                raise httpx.HTTPStatusError(
                    f"OpenAI {resp.status_code} for model={payload['model']!r} — "
                    f"body: {body}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()
            return data["choices"][0]["message"]["content"]

        except (httpx.TimeoutException, httpx.RequestError) as exc:
            # Transport-level error (DNS, connection reset, timeout). Retry.
            last_err = exc
            wait = float(2 ** attempt)
            logger.warning(
                "OpenAI transport error: %s (attempt %d/%d); retrying in %.0fs",
                type(exc).__name__, attempt + 1, max_retries, wait,
            )
            await asyncio.sleep(wait)
            continue

    raise RuntimeError(f"OpenAI call failed after {max_retries} attempts: {last_err}")


async def openai_stream(
    system: str,
    user_msg: str,
    *,
    history: list[dict] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    model: str | None = None,
) -> AsyncIterator[str]:
    """Streaming OpenAI call — yields text chunks for WebSocket."""
    payload = {
        "model": model or settings.openai_model,
        "messages": _build_messages(system, user_msg, history),
        "temperature": temperature,
        # See openai_chat() for rationale: GPT-5 family requires
        # max_completion_tokens (max_tokens is deprecated for those models).
        "max_completion_tokens": max_tokens,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", _OPENAI_URL, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    delta = chunk["choices"][0].get("delta", {})
                    text = delta.get("content")
                    if text:
                        yield text
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


async def openai_search(
    query: str,
    *,
    max_tokens: int = 1500,
    timeout: float = 60.0,
    model: str = "gpt-4o-search-preview",
) -> str:
    """Run a single OpenAI web-search-enabled chat call.

    Uses OpenAI's `gpt-4o-search-preview` model with `web_search_options={}`
    so the model performs a live web search before responding. Returns the
    assistant's message content as plain text (typically with inline
    `[1] (url)` style citations injected by the search tool).

    This is a robust replacement for third-party search libraries
    (DuckDuckGo etc.) that frequently rate-limit or hang. Failures raise.
    """
    payload = {
        "model": model,
        "web_search_options": {},
        "messages": [{"role": "user", "content": query}],
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_OPENAI_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def openai_embed(text: str) -> list[float]:
    """Generate embedding via OpenAI text-embedding-3-large."""
    logger.info("Embedding text (%d chars) with model=%s", len(text), settings.openai_embed_model)
    payload = {
        "model": settings.openai_embed_model,
        "input": text,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_OPENAI_EMBED_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.error("OpenAI embed failed: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()
        data = resp.json()
        logger.info("Embedding returned %d dimensions", len(data["data"][0]["embedding"]))
        return data["data"][0]["embedding"]


# ── Anthropic (Claude) — for heavy doc-gen + reasoning passes ──────────────

def _build_anthropic_messages(
    user_msg: str,
    history: list[dict] | None = None,
) -> list[dict]:
    """Convert OpenAI-style history into Anthropic Messages API shape.

    Anthropic uses `system` as a top-level parameter (not a message role) and
    only `user` / `assistant` roles in `messages`. We translate any system
    entries in `history` into user-prefixed text (rare in practice).
    """
    msgs: list[dict] = []
    for h in history or []:
        role = h.get("role", "user")
        content = h.get("content") or h.get("text") or h.get("q", "")
        if not content:
            continue
        if role == "system":
            # Anthropic doesn't take system inside messages; fold into user
            msgs.append({"role": "user", "content": f"[context] {content}"})
        else:
            msgs.append({"role": role, "content": str(content)})
    msgs.append({"role": "user", "content": user_msg})
    return msgs


async def anthropic_chat(
    system: str,
    user_msg: str,
    *,
    history: list[dict] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    model: str = "claude-opus-4-7",
    effort: str | None = None,
    cache_system: bool = True,
    timeout: float = 180.0,
    max_retries: int = 5,
) -> str:
    """LLM call via Anthropic for the heavy doc-gen + global-reasoning passes.

    Parameters
    ----------
    effort
        If set, enables adaptive thinking with the given effort level.
        Valid values for Opus 4.7: "low" | "medium" | "high" | "xhigh" | "max".

        - "high"  — sensible default for our doc-gen passes
        - "xhigh" — Anthropic's recommended setting for "API design, legacy
                    code migration, and large codebase reviews" (matches our
                    per-module documentation perfectly)
        - "max"   — reserved for genuinely difficult problems

        When ``effort`` is set we send ``thinking={"type": "adaptive"}`` plus
        ``output_config={"effort": effort}``. This is the API introduced in
        Opus 4.7 (April 2026) which replaced the old explicit
        ``thinking.budget_tokens`` mechanism.

        When ``effort`` is None, the model runs without adaptive thinking
        (default behaviour) — used for lower-stakes calls.
    cache_system
        If True (default), the system prompt is marked with ``cache_control:
        ephemeral``. The first call pays 1.25× the input rate to write the
        cache; subsequent calls within ~5 minutes pay 0.1× — a 90% saving on
        repeated KB / glossary contexts.

    Returns the assistant's text content. Thinking blocks are extracted
    internally and discarded — callers only see the final response text.

    Mirrors openai_chat's retry pattern: 429 with Retry-After honouring,
    5xx with exponential backoff, transport-error retry, all capped by
    max_retries.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; add it to .env")

    # ── Build system block with optional cache control ─────────────────────
    if cache_system:
        system_field: Any = [
            {"type": "text", "text": system,
             "cache_control": {"type": "ephemeral"}}
        ]
    else:
        system_field = system

    # ── Build payload ───────────────────────────────────────────────────────
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_field,
        "messages": _build_anthropic_messages(user_msg, history),
    }

    if effort:
        # Adaptive thinking (Opus 4.7+) — model decides how much to think
        # based on the configured effort level. The old explicit
        # ``budget_tokens`` parameter was removed in this generation.
        payload["thinking"] = {"type": "adaptive"}
        payload["output_config"] = {"effort": effort}
        # Anthropic still requires temperature=1.0 when thinking is enabled.
        payload["temperature"] = 1.0
        if temperature != 1.0:
            logger.debug(
                "anthropic_chat: temperature overridden to 1.0 because "
                "adaptive thinking is enabled (was %s)", temperature,
            )
    else:
        payload["temperature"] = temperature

    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_ANTHROPIC_URL, json=payload, headers=headers)

            if resp.status_code == 429:
                ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
                if ra and ra.replace(".", "").isdigit():
                    wait = float(ra) + 1.0
                else:
                    wait = 20.0 * (attempt + 1)
                logger.warning(
                    "Anthropic 429 rate-limited (attempt %d/%d); waiting %.0fs",
                    attempt + 1, max_retries, wait,
                )
                await asyncio.sleep(min(wait, 120.0))
                last_err = RuntimeError(f"429 (attempt {attempt + 1})")
                continue

            # Anthropic returns 529 for "overloaded" — treat as retryable
            if 500 <= resp.status_code < 600 or resp.status_code == 529:
                wait = float(2 ** attempt)
                logger.warning(
                    "Anthropic %d (attempt %d/%d); retrying in %.0fs",
                    resp.status_code, attempt + 1, max_retries, wait,
                )
                last_err = RuntimeError(
                    f"{resp.status_code} {resp.text[:200]} (attempt {attempt + 1})"
                )
                await asyncio.sleep(wait)
                continue

            # ── 4xx (other) or 2xx: process normally ─────────────────────────
            # On 4xx, surface the response body so the caller sees the actual
            # Anthropic error (e.g. wrong model name, parameter mismatch),
            # not just "400 Bad Request".
            if 400 <= resp.status_code < 500:
                body = resp.text[:500] if resp.text else "(empty body)"
                raise httpx.HTTPStatusError(
                    f"Anthropic {resp.status_code} for model={payload['model']!r} — "
                    f"body: {body}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()

            # ── Extract the text content; skip thinking blocks ─────────────
            # data["content"] is a list of blocks: thinking, text, tool_use, etc.
            text_parts: list[str] = []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            return "".join(text_parts)

        except (httpx.TimeoutException, httpx.RequestError) as exc:
            last_err = exc
            wait = float(2 ** attempt)
            logger.warning(
                "Anthropic transport error: %s (attempt %d/%d); retrying in %.0fs",
                type(exc).__name__, attempt + 1, max_retries, wait,
            )
            await asyncio.sleep(wait)
            continue

    raise RuntimeError(f"Anthropic call failed after {max_retries} attempts: {last_err}")


# ── Provider-agnostic dispatcher ────────────────────────────────────────────

async def chat(
    model_spec: str,
    system: str,
    user_msg: str,
    *,
    history: list[dict] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """Route to openai_chat or anthropic_chat based on the ``model_spec``.

    Model spec format::

        "openai:gpt-5.5"                     → OpenAI GPT-5.5 (reasoning;
                                                temperature locked at 1)
        "openai:gpt-5.4-mini"                → OpenAI GPT-5.4 Mini (allows
                                                custom temperature)
        "anthropic:claude-opus-4-7"          → Claude Opus 4.7 (no adaptive
                                                thinking)
        "anthropic:claude-opus-4-7:effort=high"
                                             → Claude Opus 4.7 with adaptive
                                                thinking at "high" effort
        "anthropic:claude-opus-4-7:effort=xhigh"
                                             → recommended for technical docs
        "anthropic:claude-haiku-4-5"         → Claude Haiku 4.5

    OpenAI calls automatically request JSON mode (response_format=json_object).
    Anthropic calls automatically cache the system message (cache_control=
    ephemeral) — first call pays 1.25× write, subsequent calls 0.1× read.

    Returns the raw model text. Callers do their own JSON parsing (typically
    via ``parse_json_response``).
    """
    parts = model_spec.split(":")
    if len(parts) < 2:
        raise ValueError(
            f"Invalid model_spec {model_spec!r}; expected 'provider:model[:opt=val]'"
        )
    provider, model_name = parts[0], parts[1]
    opts = parts[2:]

    if provider == "openai":
        # All bulk-tagging / doc-gen calls use JSON mode for reliable parsing.
        return await openai_chat(
            system, user_msg,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model_name,
            response_format={"type": "json_object"},
        )

    if provider == "anthropic":
        effort: str | None = None
        for opt in opts:
            if opt.startswith("effort="):
                effort = opt.split("=", 1)[1].strip().lower()
                if effort not in ("low", "medium", "high", "xhigh", "max"):
                    raise ValueError(
                        f"Invalid effort {effort!r} in {model_spec!r}; "
                        "must be one of low|medium|high|xhigh|max"
                    )
        return await anthropic_chat(
            system, user_msg,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model_name,
            effort=effort,
            cache_system=True,
        )

    raise ValueError(
        f"Unknown provider {provider!r} in {model_spec!r} "
        "(expected 'openai' or 'anthropic')"
    )


def parse_json_response(text: str) -> dict[str, Any]:
    """Strip markdown fences and parse JSON from LLM output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end])
        raise
