"""Repositorio Qdrant – rag-service (lectura/búsqueda)."""
import structlog
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient
from shared.config import get_secret, get_settings
from shared.fakes import fake_embedding

logger = structlog.get_logger(__name__)


class QdrantRepository:
    """Encapsula la conexion con Qdrant para operaciones de busqueda."""

    def __init__(self) -> None:
        settings = get_settings()
        # API key obtenida desde Key Vault; no se almacena en variables de entorno
        api_key = None if settings.use_fake_integrations else get_secret("qdrant-api-key")
        # Cliente asincrono para no bloquear el event loop durante las consultas
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=api_key,
            check_compatibility=not settings.use_fake_integrations,
        )
        self._collection = settings.qdrant_collection
        self._use_fake_integrations = settings.use_fake_integrations

    async def get_vector_store(self) -> QdrantVectorStore:
        """Devuelve el adaptador de LlamaIndex que envuelve la coleccion de Qdrant."""
        # aclient usa el cliente asincrono para mantener la consistencia del servicio
        return QdrantVectorStore(
            aclient=self._client,
            collection_name=self._collection,
        )

    async def search_fake(self, query_text: str, limit: int):
        """Busca directamente en Qdrant usando embeddings deterministas locales."""
        if not await self._client.collection_exists(self._collection):
            logger.info(
                "qdrant_search_completed",
                collection=self._collection,
                points=0,
                collection_exists=False,
            )
            return []

        response = await self._client.query_points(
            collection_name=self._collection,
            query=fake_embedding(query_text),
            limit=limit,
            with_payload=True,
        )
        logger.info(
            "qdrant_search_completed",
            collection=self._collection,
            points=len(response.points),
            collection_exists=True,
        )
        return response.points
