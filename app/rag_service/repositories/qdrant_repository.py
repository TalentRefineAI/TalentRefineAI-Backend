"""Repositorio Qdrant – rag-service (lectura/búsqueda)."""
import time as _time
from qdrant_client import AsyncQdrantClient

# Module-level TTL cache for list_candidates (avoids repeated full-scroll calls)
_cand_cache: list[dict] = []
_cand_cache_ts: float = 0.0
_CAND_TTL: float = 60.0

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
        - Resultado cacheado en memoria 60 s para reducir llamadas a Qdrant.
        """
        global _cand_cache, _cand_cache_ts
        if _cand_cache and (_time.monotonic() - _cand_cache_ts) < _CAND_TTL:
            return list(_cand_cache)

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
                limit=500,
                offset=offset,
            )
            for point in result:
                payload = point.payload or {}
                cid = payload.get("candidate_id")
                if not cid or cid in seen:
                    continue
                seen.add(cid)

                # Nombre limpio desde file_name (fallback si el payload no trae nombre)
                raw_fname = payload.get("file_name", "")
                fname = raw_fname.split("/")[-1] if "/" in raw_fname else raw_fname
                name_from_file = fname.rsplit(".", 1)[0] if "." in fname else fname or cid

                # Metadatos denormalizados en el payload (extraidos en el procesamiento).
                # Fallback retrocompatible para datos antiguos: nombre desde filename,
                # email por regex sobre el chunk de texto.
                contact = payload.get("contact_info") or {}
                name = (contact.get("name") or "").strip() or name_from_file
                email = (contact.get("email") or "").strip()
                if not email:
                    m = _EMAIL.search(payload.get("chunk_text", ""))
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

        _cand_cache = list(candidates)
        _cand_cache_ts = _time.monotonic()
        return candidates

    async def delete_all(self) -> None:
        """Elimina toda la colección de Qdrant (borra la collection entera)."""
        global _cand_cache_ts
        _cand_cache_ts = 0.0  # invalidar cache de candidatos
        if await self._client.collection_exists(self._collection):
            await self._client.delete_collection(self._collection)
