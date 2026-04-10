"""Test Qdrant search for Apex docs.

Run this script to:
- List distinct `module` values in the `qad_docs` collection
- Run a filtered search using `module='purchase'` for the question
- Run an unfiltered search to compare results

Usage:
    python -m scripts.test_qdrant_search
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from textwrap import shorten

from qdrant_client import QdrantClient

from app.core.config import settings
from app.vector.qdrant import search_chunks


LOG = logging.getLogger("test_qdrant_search")


def list_modules(collection: str = "qad_docs") -> Counter:
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    LOG.info("Listing points in collection=%s (limit=1000)", collection)
    counter = Counter()
    try:
        resp = client.scroll(collection_name=collection, with_payload=True, limit=1000)
        # qdrant-client `scroll` may return an object with `.points` or a tuple; handle both
        if hasattr(resp, "points"):
            points = resp.points
        elif isinstance(resp, (list, tuple)) and resp:
            # tuple form often (points, next_page)
            points = resp[0]
        else:
            points = []

        for p in points:
            payload = getattr(p, "payload", None) or getattr(p, "payload", {}) or (p.get("payload") if isinstance(p, dict) else {})
            m = payload.get("module") or payload.get("Module") or "<missing>"
            counter[m] += 1
    except Exception as exc:
        LOG.exception("Failed to list modules: %s", exc)
    return counter


async def run_search(question: str, collection: str = "qad_docs", modules: list[str] | None = None):
    LOG.info("Searching collection=%s modules=%s question=%s", collection, modules, question)
    try:
        results = await search_chunks(question, collection=collection, modules=modules, top_k=10)
    except Exception as exc:
        LOG.exception("Search failed: %s", exc)
        return []

    LOG.info("Search returned %d results", len(results))
    for i, r in enumerate(results, start=1):
        meta = r.get("metadata", {})
        text = r.get("text", "")
        LOG.info("%02d: module=%s file=%s score=%.4f snippet=%s", i, meta.get("module"), meta.get("filename"), float(r.get("score") or 0), shorten(text.replace('\n', ' '), width=200))
    return results


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    collection = "qad_docs"

    LOG.info("Settings: qdrant_url=%s collection=%s", settings.qdrant_url, collection)

    modules = list_modules(collection=collection)
    LOG.info("Found modules: %s", dict(modules))

    question = "how to create purchase order"

    # Run filtered search with module 'purchase'
    asyncio.run(run_search(question, collection=collection, modules=["purchase"]))

    # Run unfiltered search
    asyncio.run(run_search(question, collection=collection, modules=None))


if __name__ == "__main__":
    main()
