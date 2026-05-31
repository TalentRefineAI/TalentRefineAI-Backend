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
            timeout=60,
            check_compatibility=False,
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

    async def list_candidates(self) -> list[dict]:
        """
        Recorre todos los puntos de Qdrant y devuelve candidatos únicos.
        - Deduplica por candidate_id.
        - Nombre: filename limpio (sin prefijo UUID ni extensión).
        - Email: extraído por regex del chunk de texto; sin llamadas a LLM.
        """
        import re
        _EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

        if not await self._client.collection_exists(self._collection):
            return []

        seen: set[str] = set()
        candidates: list[dict] = []
        offset = None

        while True:
            result, next_offset = await self._client.scroll(
                collection_name=self._collection,
                with_payload=True,
                with_vectors=False,
                limit=100,
                offset=offset,
            )
            for point in result:
                payload = point.payload or {}
                cid = payload.get("candidate_id")
                if not cid or cid in seen:
                    continue
                seen.add(cid)

                # Nombre limpio desde file_name
                raw_fname = payload.get("file_name", "")
                fname = raw_fname.split("/")[-1] if "/" in raw_fname else raw_fname
                name = fname.rsplit(".", 1)[0] if "." in fname else fname or cid

                # Email extraído por regex desde el chunk de texto
                chunk = payload.get("chunk_text", "")
                m = _EMAIL.search(chunk)
                email = m.group() if m else ""

                candidates.append({
                    "candidate_id": cid,
                    "name": name,
                    "email": email,
                    "file_name": fname,
                })

            if next_offset is None:
                break
            offset = next_offset

        return candidates

    async def delete_all(self) -> None:
        """Elimina toda la colección de Qdrant (borra la collection entera)."""
        if await self._client.collection_exists(self._collection):
            await self._client.delete_collection(self._collection)
