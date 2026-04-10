"""Apex — floating RAG chatbot trained on QAD user guides + embedded custom docs.

Domain routing:
  - Standard domains (sales, purchasing, manufacturing) → qdrant_collection_apex
  - custom_docs domain → qad_custom_docs collection
  - Mixed → searches both and merges results
"""
from __future__ import annotations

import logging

from fastapi import WebSocket

from app.core.config import settings
from app.core.llm import openai_stream
from app.core.session import append_turn, get_context, load_history, set_context
from app.core.ws import send_done, send_error, send_frame, send_status, send_token
from app.vector.qdrant import search_chunks

logger = logging.getLogger(__name__)

AGENT_KEY = "apex"
CUSTOM_DOCS_COLLECTION = "qad_custom_docs"

SYSTEM_PROMPT = """You are Apex, a helpful QAD ERP assistant that answers questions based on official user guide documentation and custom module documentation.

RULES:
- Answer ONLY based on the provided documentation chunks. If the answer isn't in the chunks, say "I don't have information about that in the available documentation."
- Be concise but thorough. Use bullet points for steps.
- If the user asks a follow-up, use conversation history for context.
- At the end of your answer, suggest 1-2 relevant follow-up questions the user might want to ask.
- Format follow-up questions on separate lines starting with ">>>" like:
  >>> How do I approve a purchase order?
  >>> What are the PO status codes?

DOCUMENTATION CHUNKS:
{chunks}
"""


async def _search_all(question: str, domains: list[str]) -> list[dict]:
    """Search across standard Apex collection and/or custom_docs collection."""
    standard_domains = [d for d in domains if d != "custom_docs"]
    wants_custom     = "custom_docs" in domains
    wants_standard   = bool(standard_domains) or (not domains)  # no filter = all

    all_chunks: list[dict] = []

    # Standard QAD user guide collection
    if wants_standard:
        try:
            chunks = await search_chunks(
                question,
                collection=settings.qdrant_collection_apex,
                modules=standard_domains if standard_domains else None,
                top_k=8,
            )
            all_chunks.extend(chunks)
        except Exception as exc:
            logger.warning("Standard Apex search failed: %s", exc)

    # Custom modules collection
    if wants_custom:
        try:
            chunks = await search_chunks(
                question,
                collection=CUSTOM_DOCS_COLLECTION,
                modules=None,   # search all modules within custom_docs
                top_k=6,
            )
            # Tag so we can distinguish in the source display
            for c in chunks:
                c["metadata"]["collection"] = "custom_docs"
            all_chunks.extend(chunks)
        except Exception as exc:
            logger.warning("Custom docs search failed: %s", exc)

    # Sort by score descending, take top 10
    all_chunks.sort(key=lambda c: c.get("score", 0), reverse=True)
    return all_chunks[:10]


async def handle_apex_ws(ws: WebSocket, session_id: str, user: dict) -> None:
    """Main WebSocket handler for Apex."""
    logger.info("Apex WS handler started for session %s", session_id)
    try:
        while True:
            data = await ws.receive_json()
            logger.info("Apex received: %s", data)
            question = (data.get("question") or "").strip()
            if not question:
                await send_error(ws, "Question is required")
                continue

            # Determine domains from message or existing context
            raw_domains = data.get("domains")
            ctx = get_context(session_id, AGENT_KEY) or {}
            if raw_domains is not None:
                domains = raw_domains
            else:
                domains = ctx.get("domains", [])

            # Normalise
            mapping = {
                "purchase": "purchasing", "purchases": "purchasing",
                "sale": "sales", "mfg": "manufacturing",
            }
            normalised: list[str] = []
            for d in (domains or []):
                if not isinstance(d, str):
                    continue
                key = d.strip().lower()
                normalised.append(mapping.get(key, key))

            if normalised:
                set_context(session_id, AGENT_KEY, {"domains": normalised})
            domains = normalised

            await send_status(ws, "Searching documentation…")
            logger.info("Searching Qdrant — domains=%s", domains)

            try:
                chunks = await _search_all(question, domains)
                logger.info("Total chunks retrieved: %d", len(chunks))
            except Exception as exc:
                logger.exception("Qdrant search failed")
                await send_error(ws, f"Search failed: {exc}")
                await send_done(ws)
                continue

            chunks_text = "\n\n---\n\n".join(
                f"[Source: {c['metadata'].get('module', 'unknown')} | "
                f"{c['metadata'].get('filename', c['metadata'].get('section', 'unknown'))}]\n{c['text']}"
                for c in chunks
            )

            sources = [
                {
                    "module":   c["metadata"].get("module", ""),
                    "filename": c["metadata"].get("filename", c["metadata"].get("section", "")),
                    "score":    round(c["score"], 3),
                }
                for c in chunks[:5]
            ]

            history = load_history(session_id, AGENT_KEY)
            chat_history = []
            for h in history[-10:]:
                chat_history.append({"role": "user",      "content": h.get("q", "")})
                if h.get("a"):
                    chat_history.append({"role": "assistant", "content": h["a"]})

            system = SYSTEM_PROMPT.format(chunks=chunks_text or "No relevant documentation found.")

            await send_status(ws, "Generating answer…")

            full_answer: list[str] = []
            followups:   list[str] = []
            try:
                async for token in openai_stream(system, question, history=chat_history):
                    full_answer.append(token)
                    await send_token(ws, token)
            except Exception as exc:
                logger.exception("OpenAI stream failed")
                await send_error(ws, f"LLM error: {exc}")
                await send_done(ws)
                continue

            answer_text = "".join(full_answer)

            for line in answer_text.split("\n"):
                stripped = line.strip()
                if stripped.startswith(">>>"):
                    followups.append(stripped[3:].strip())

            await send_frame(ws, "sources",  sources)
            if followups:
                await send_frame(ws, "followup", followups)

            append_turn(session_id, AGENT_KEY, {"q": question, "a": answer_text})
            await send_done(ws)

    except Exception as exc:
        logger.exception("Apex WS error: %s", exc)
        try:
            await send_error(ws, str(exc))
            await send_done(ws)
        except Exception:
            pass
