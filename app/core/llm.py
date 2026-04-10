"""Unified LLM client — routes to Groq (free/fast) or OpenAI (quality).

Usage::

    from app.core.llm import groq_chat, openai_chat, openai_stream, openai_embed

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

import json
import logging
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"


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
) -> str:
    """Quality LLM call via OpenAI for SQL gen, RAG answers, doc gen."""
    payload = {
        "model": model or settings.openai_model,
        "messages": _build_messages(system, user_msg, history),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(_OPENAI_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


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
