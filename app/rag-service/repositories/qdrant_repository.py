"""Repositorio Qdrant – rag-service (lectura/búsqueda)."""
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient

from shared.config import get_secret, get_settings


class QdrantRepository:
    """Encapsula la conexion con Qdrant para operaciones de busqueda."""

    def __init__(self) -> None:
        settings = get_settings()
        # API key obtenida desde Key Vault; no se almacena en variables de entorno
        api_key = get_secret("qdrant-api-key")
        # Cliente asincrono para no bloquear el event loop durante las consultas
        self._client = AsyncQdrantClient(url=settings.qdrant_url, api_key=api_key)
        self._collection = settings.qdrant_collection

    async def get_vector_store(self) -> QdrantVectorStore:
        """Devuelve el adaptador de LlamaIndex que envuelve la coleccion de Qdrant."""
        # aclient usa el cliente asincrono para mantener la consistencia del servicio
        return QdrantVectorStore(
            aclient=self._client,
            collection_name=self._collection,
        )
