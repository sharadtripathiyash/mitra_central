"""Embed PDFs from data/apex-pdf into Qdrant collection `qad_docs`.

Usage:
    python -m scripts.embed_qad_docs

This script:
- Scans `data/apex-pdf` for PDF files
- Extracts text using PyPDF2
- Splits into chunks of ~1000 characters with 200 overlap
- Embeds each chunk with OpenAI `text-embedding-3-large` via `app.core.llm.openai_embed`
- Upserts chunks to Qdrant collection `qad_docs` with payload fields: `text`, `module`, `filename`
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import List

from PyPDF2 import PdfReader

from app.core.config import settings
from app.core.llm import openai_embed
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance

logger = logging.getLogger("embed_qad_docs")


DATA_DIR = Path.cwd() / "data" / "apex-pdf"
COLLECTION = "qad_docs"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


def extract_text_from_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(pages)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        chunk = text[start:end]
        chunks.append(chunk.strip())
        if end == length:
            break
        start = max(0, end - overlap)
    return chunks


async def embed_and_upsert():
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)

    # Determine embedding dim by embedding a small sample
    sample = "hello"
    dim = len(await openai_embed(sample))

    # Recreate collection (delete if exists)
    try:
        client.delete_collection(collection_name=COLLECTION)
    except Exception:
        pass

    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )

    files = sorted(DATA_DIR.glob("*.pdf"))
    if not files:
        logger.info("No PDF files found in %s", DATA_DIR)
        return

    point_id = 1
    batch: List[PointStruct] = []
    batch_size = 64

    for f in files:
        logger.info("Processing %s", f.name)
        text = extract_text_from_pdf(f)
        chunks = chunk_text(text)
        module_name = f.stem.lower()

        for ch in chunks:
            vec = await openai_embed(ch)
            payload = {
                "text": ch,
                "module": module_name,
                "filename": f.name,
            }
            batch.append(PointStruct(id=str(uuid.uuid4()), vector=vec, payload=payload))

            if len(batch) >= batch_size:
                client.upsert(collection_name=COLLECTION, points=batch)
                logger.info("Upserted %d points", len(batch))
                batch = []

    if batch:
        client.upsert(collection_name=COLLECTION, points=batch)
        logger.info("Upserted final %d points", len(batch))

    logger.info("Embedding complete. Collection=%s", COLLECTION)


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(embed_and_upsert())


if __name__ == "__main__":
    main()
