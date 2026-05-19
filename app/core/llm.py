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
    payload: dict[str, Any] = {
        "model": model or settings.openai_model,
        "messages": _build_messages(system, user_msg, history),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
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
            resp.raise_for_status()
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
        "max_tokens": max_tokens,
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
    thinking_budget: int | None = None,
    cache_system: bool = True,
    timeout: float = 180.0,
    max_retries: int = 5,
) -> str:
    """LLM call via Anthropic for the heavy doc-gen + global-reasoning passes.

    Parameters
    ----------
    thinking_budget
        If set, enables extended thinking. The model spends this many tokens
        reasoning internally before producing output (Claude 4+ feature).
        Anthropic requires temperature=1.0 when thinking is enabled — we
        force it for you (the temperature argument is ignored if thinking is
        on, with a debug log so it's visible).
    cache_system
        If True (default), the system prompt is marked with `cache_control:
        ephemeral`. The first call pays 1.25× the input rate to write the
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

    if thinking_budget and thinking_budget > 0:
        # Extended thinking adds output tokens — pad max_tokens to fit both
        # the thinking budget and the user's requested output budget.
        payload["max_tokens"] = max_tokens + thinking_budget
        payload["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        # Anthropic requires temperature=1.0 when thinking is enabled.
        payload["temperature"] = 1.0
        if temperature != 1.0:
            logger.debug(
                "anthropic_chat: temperature overridden to 1.0 because "
                "thinking is enabled (was %s)", temperature,
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

            resp.raise_for_status()
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

        "openai:gpt-5.5"                     → OpenAI GPT-5.5
        "openai:gpt-5.4-mini"                → OpenAI GPT-5.4 Mini
        "anthropic:claude-opus-4-7"          → Claude Opus 4.7 (no thinking)
        "anthropic:claude-opus-4-7:thinking=8000"
                                             → Claude Opus 4.7 with 8K-token
                                                extended thinking budget
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
        thinking_budget: int | None = None
        for opt in opts:
            if opt.startswith("thinking="):
                try:
                    thinking_budget = int(opt.split("=", 1)[1])
                except ValueError:
                    raise ValueError(f"Invalid thinking budget in {model_spec!r}")
        return await anthropic_chat(
            system, user_msg,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model_name,
            thinking_budget=thinking_budget,
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
