"""Create payload index for Qdrant collection `qad_docs` to enable metadata filtering.

This helper attempts to create an index on the `module` payload field. If the
installed qdrant-client version exposes `create_payload_index`, it will be used.
Otherwise the script prints a message describing manual steps.
"""
from __future__ import annotations

import logging

from app.core.config import settings
from qdrant_client import QdrantClient

logger = logging.getLogger("create_qdrant_index")


def main():
    logging.basicConfig(level=logging.INFO)
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    coll = "qad_docs"
    try:
        # Try convenient helper if present. Newer Qdrant versions require an explicit
        # `field_schema` so we pass type=keyword which matches short categorical values.
        if hasattr(client, "create_payload_index"):
            logger.info("Creating payload index for 'module' on %s", coll)
            try:
                client.create_payload_index(
                    collection_name=coll,
                    field_name="module",
                    field_schema={"type": "keyword"},
                )
                logger.info("Index created")
            except Exception:
                # Retry with lower-level call if helper fails; raise to outer handler
                raise
        else:
            logger.warning(
                "qdrant-client has no create_payload_index helper. You may need to create a keyword index via the Qdrant REST API or UI for field 'module'."
            )
    except Exception as exc:
        logger.exception("Failed to create payload index: %s", exc)


if __name__ == "__main__":
    main()
