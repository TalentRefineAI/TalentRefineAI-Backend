"""Repositorio Qdrant – rag-service (lectura/búsqueda)."""
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient

from shared.config import get_secret, get_settings


class QdrantRepository:
    """Encapsula la conexión con Qdrant para operaciones de búsqueda."""

    def __init__(self) -> None:
        settings = get_settings()
        api_key = get_secret("qdrant-api-key")
        self._client = AsyncQdrantClient(url=settings.qdrant_url, api_key=api_key)
        self._collection = settings.qdrant_collection

    async def get_vector_store(self) -> QdrantVectorStore:
        """Devuelve el vector store de LlamaIndex apuntando a la colección configurada."""
        return QdrantVectorStore(
            aclient=self._client,
            collection_name=self._collection,
        )
