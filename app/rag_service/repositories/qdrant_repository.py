"""Repositorio Qdrant – rag-service (lectura/búsqueda)."""
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient

from shared.config import get_secret, get_settings
from shared.fakes import fake_embedding


class QdrantRepository:
    """Encapsula la conexion con Qdrant para operaciones de busqueda."""

    def __init__(self) -> None:
        settings = get_settings()
        self._use_fake = settings.use_fake_integrations
        api_key = get_secret("qdrant-api-key")
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=api_key or None,
        )
        self._collection = settings.qdrant_collection

    async def get_vector_store(self) -> QdrantVectorStore:
        """Devuelve el adaptador de LlamaIndex que envuelve la coleccion de Qdrant."""
        # aclient usa el cliente asincrono para mantener la consistencia del servicio
        return QdrantVectorStore(
            aclient=self._client,
            collection_name=self._collection,
        )

    async def search_fake(self, query_text: str, limit: int) -> list:
        """Busqueda fake usando fake_embedding. Para uso en local sin Azure."""
        if not await self._client.collection_exists(self._collection):
            return []
        response = await self._client.query_points(
            collection_name=self._collection,
            query=fake_embedding(query_text),
            limit=limit,
            with_payload=True,
        )
        return response.points
