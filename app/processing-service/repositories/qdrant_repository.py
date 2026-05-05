"""Repositorio Qdrant – processing-service."""
import uuid
from typing import Any

import structlog
from llama_index.core.schema import BaseNode
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct

from shared.config import get_secret, get_settings
from shared.models import CandidatePayload

logger = structlog.get_logger(__name__)


class QdrantRepository:
    """Encapsula operaciones de escritura sobre Qdrant."""

    def __init__(self) -> None:
        settings = get_settings()
        api_key = get_secret("qdrant-api-key")
        self._client = AsyncQdrantClient(url=settings.qdrant_url, api_key=api_key)
        self._collection = settings.qdrant_collection

    async def upsert_nodes(
        self,
        doc_id: uuid.UUID,
        nodes: list[BaseNode],
        doc_metadata: dict[str, Any],
    ) -> None:
        """
        Persiste los chunks como puntos en Qdrant.
        Cada nodo genera un punto con su embedding y el payload mínimo definido.
        """
        points: list[PointStruct] = []

        for node in nodes:
            embedding = node.embedding
            if embedding is None:
                logger.warning("node_without_embedding", node_id=node.node_id)
                continue

            payload = CandidatePayload(
                candidate_id=str(doc_id),
                file_name=doc_metadata.get("file_name", ""),
                contact_info={},
                exp_years=0,
                location=None,
                chunk_text=node.get_content(),
            ).model_dump()

            points.append(
                PointStruct(
                    id=node.node_id,
                    vector=embedding,
                    payload=payload,
                )
            )

        if points:
            await self._client.upsert(collection_name=self._collection, points=points)
            logger.info("qdrant_upsert", collection=self._collection, points=len(points))
