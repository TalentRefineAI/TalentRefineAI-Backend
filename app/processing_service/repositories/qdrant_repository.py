"""Repositorio Qdrant – processing-service."""
import uuid
from typing import Any

import structlog
from llama_index.core.schema import BaseNode
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from shared.config import get_secret, get_settings
from shared.fakes import fake_embedding
from shared.models import CandidatePayload

logger = structlog.get_logger(__name__)


class QdrantRepository:
    """Encapsula operaciones de escritura sobre Qdrant."""

    def __init__(self) -> None:
        settings = get_settings()
        # La API key se obtiene desde Key Vault, nunca del entorno directamente
        api_key = None if settings.use_fake_integrations else get_secret("qdrant-api-key")
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=api_key,
            check_compatibility=not settings.use_fake_integrations,
        )
        self._collection = settings.qdrant_collection
        self._use_fake_integrations = settings.use_fake_integrations
        self._vector_size = settings.fake_embedding_dimensions

    async def _ensure_collection(self) -> None:
        if not self._use_fake_integrations:
            return

        exists = await self._client.collection_exists(self._collection)
        if not exists:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
            )
            logger.info("qdrant_collection_created", collection=self._collection)

    async def upsert_nodes(
        self,
        doc_id: uuid.UUID,
        nodes: list[BaseNode],
        doc_metadata: dict[str, Any],
    ) -> None:
        """
        Persiste los chunks como puntos en Qdrant.
        Cada nodo genera un punto con su embedding y el payload minimo definido.
        """
        await self._ensure_collection()
        points: list[PointStruct] = []

        for node in nodes:
            content = node.get_content()
            embedding = node.embedding
            if embedding is None and self._use_fake_integrations:
                embedding = fake_embedding(content)
                logger.info(
                    "fake_embeddings_generated",
                    doc_id=str(doc_id),
                    node_id=node.node_id,
                    dimensions=len(embedding),
                )

            # Si un nodo no tiene embedding no se puede indexar; se omite con aviso
            if embedding is None:
                logger.warning("node_without_embedding", node_id=node.node_id)
                continue

            # Construye el payload siguiendo el esquema minimo acordado
            payload = CandidatePayload(
                candidate_id=str(doc_id),
                file_name=doc_metadata.get("file_name", ""),
                contact_info={},   # se enriquecera en fases posteriores
                exp_years=0,       # se calculara en fases posteriores
                location=None,
                chunk_text=content,
            ).model_dump()

            points.append(
                PointStruct(
                    id=node.node_id,
                    vector=embedding,
                    payload=payload,
                )
            )

        if points:
            # Upsert en bloque para reducir el numero de llamadas a la API
            await self._client.upsert(collection_name=self._collection, points=points)
            logger.info(
                "qdrant_upsert_completed",
                doc_id=str(doc_id),
                collection=self._collection,
                points=len(points),
            )
