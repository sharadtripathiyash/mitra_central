"""Qdrant vector search client for Apex RAG.

Embeds the query via OpenAI text-embedding-3-large, then searches the given
Qdrant collection with optional metadata filters.
"""
from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny

from app.core.config import settings
from app.core.llm import openai_embed

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None


def get_qdrant() -> QdrantClient:
    global _client
    if _client is None:
        logger.info("Connecting to Qdrant at %s", settings.qdrant_url)
        _client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=30,
        )
    return _client


async def search_chunks(
    query: str,
    *,
    collection: str | None = None,
    modules: list[str] | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Embed query and search Qdrant. Returns list of {text, metadata, score}."""
    coll = collection or settings.qdrant_collection_apex
    logger.info("Embedding query for Qdrant search...")
    vector = await openai_embed(query)

    query_filter = None
    if modules:
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="module",
                    match=MatchAny(any=modules),
                )
            ]
        )

    client = get_qdrant()
    logger.info("Searching Qdrant collection=%s, filter=%s, top_k=%d", coll, modules, top_k)

    # Run synchronous Qdrant search in a thread to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(
            None,
            partial(
                client.search,
                collection_name=coll,
                query_vector=vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            ),
        )
    except Exception as exc:
        # If filter failed due to missing payload index, retry without filter.
        # Permanent fix: run create_qdrant_index.py once to create the index.
        if query_filter is not None and "Index required" in str(exc):
            logger.warning(
                "Qdrant 'module' filter failed (payload index missing). "
                "Retrying without filter. Run create_qdrant_index.py to fix permanently."
            )
            results = await loop.run_in_executor(
                None,
                partial(
                    client.search,
                    collection_name=coll,
                    query_vector=vector,
                    query_filter=None,
                    limit=top_k,
                    with_payload=True,
                ),
            )
        else:
            raise

    logger.info("Qdrant returned %d results", len(results))
    chunks = []
    for r in results:
        payload = r.payload or {}
        chunks.append({
            "text": payload.get("text", payload.get("content", "")),
            "metadata": {k: v for k, v in payload.items() if k not in ("text", "content")},
            "score": r.score,
        })
    # If no results returned and the queried collection is the configured Apex
    # collection, attempt a fallback search against a commonly-used collection
    # `qad_docs` which may have been created by the local embed script.
    if not chunks and coll != "qad_docs":
        logger.info("No results in %s; attempting fallback search in 'qad_docs'", coll)
        try:
            results2 = await loop.run_in_executor(
                None,
                partial(
                    client.search,
                    collection_name="qad_docs",
                    query_vector=vector,
                    query_filter=query_filter,
                    limit=top_k,
                    with_payload=True,
                ),
            )
        except Exception:
            results2 = []

        logger.info("Fallback Qdrant returned %d results", len(results2))
        for r in results2:
            payload = r.payload or {}
            chunks.append({
                "text": payload.get("text", payload.get("content", "")),
                "metadata": {k: v for k, v in payload.items() if k not in ("text", "content")},
                "score": r.score,
            })
    return chunks