"""Repositorio Qdrant – processing-service (escritura)."""
import uuid
from typing import Any

import structlog
from llama_index.core.schema import BaseNode
from llama_index.core.vector_stores.utils import node_to_metadata_dict
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from shared.config import get_secret, get_settings
from shared.fakes import fake_embedding
from shared.models import CandidatePayload

logger = structlog.get_logger(__name__)


class QdrantRepository:
    """Encapsula operaciones de escritura sobre Qdrant."""

    def __init__(self) -> None:
        settings = get_settings()
        self._use_fake = settings.use_fake_integrations
        self._collection = settings.qdrant_collection
        self._vector_size = settings.fake_embedding_dimensions
        api_key = get_secret("qdrant-api-key")
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=api_key or None,
            timeout=60,
            check_compatibility=False,
        )

    async def _ensure_collection(self) -> None:
        """Crea la coleccion si no existe."""
        logger.info("qdrant_checking_collection", collection=self._collection)
        try:
            exists = await self._client.collection_exists(self._collection)
        except Exception as e:
            logger.error("qdrant_collection_check_failed", exc_type=type(e).__name__, exc=repr(e))
            raise
        logger.info("qdrant_collection_exists", exists=exists)
        if not exists:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
            )
            logger.info("qdrant_collection_created", collection=self._collection)
            # Índice en candidate_id para acelerar scroll+dedup en list_candidates
            from qdrant_client.models import PayloadSchemaType
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name="candidate_id",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            logger.info("qdrant_payload_index_created", field="candidate_id")

    async def upsert_nodes(
        self,
        doc_id: uuid.UUID,
        nodes: list[BaseNode],
        doc_metadata: dict[str, Any],
    ) -> int:
        """
        Persiste los chunks como puntos en Qdrant.
        En modo fake genera embeddings deterministicos si el nodo no los tiene.
        Devuelve el numero de puntos realmente upserted (0 si no hubo ninguno).
        """
        await self._ensure_collection()
        points: list[PointStruct] = []

        for node in nodes:
            content = node.get_content()
            embedding = node.embedding
            if embedding is None:
                if self._use_fake:
                    embedding = fake_embedding(content)
                    logger.info("fake_embedding_generated", node_id=node.node_id, dims=len(embedding))
                else:
                    logger.warning("node_without_embedding", node_id=node.node_id)
                    continue

            # Denormaliza los metadatos del candidato en CADA chunk para que el panel
            # y la busqueda los lean del payload sin depender de que chunk se recupere.
            payload = CandidatePayload(
                candidate_id=str(doc_id),
                file_name=doc_metadata.get("file_name", ""),
                contact_info=doc_metadata.get("contact_info") or {},
                exp_years=int(doc_metadata.get("exp_years") or 0),
                location=doc_metadata.get("location"),
                chunk_text=content,
            ).model_dump()
            # Add LlamaIndex-required fields so the RAG retriever can reconstruct TextNodes
            node_meta = node_to_metadata_dict(node)
            payload["_node_content"] = node_meta["_node_content"]
            payload["_node_type"] = node_meta["_node_type"]

            points.append(PointStruct(id=node.node_id, vector=embedding, payload=payload))

        if points:
            # Elimina puntos anteriores del mismo candidato para evitar duplicados
            await self._client.delete(
                collection_name=self._collection,
                points_selector=Filter(
                    must=[FieldCondition(key="candidate_id", match=MatchValue(value=str(doc_id)))]
                ),
            )
            logger.info("qdrant_upserting", n_points=len(points))
            await self._client.upsert(collection_name=self._collection, points=points)
            logger.info("qdrant_upsert", collection=self._collection, points=len(points))

        return len(points)
