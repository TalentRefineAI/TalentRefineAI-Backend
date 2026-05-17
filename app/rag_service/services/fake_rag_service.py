"""Servicio RAG local fake para pruebas de integracion sin Azure OpenAI."""

import structlog
from shared.exceptions import VectorStoreError

from ..repositories.qdrant_repository import QdrantRepository
from ..routers.schemas import CandidateResult, SearchFilters

logger = structlog.get_logger(__name__)


class FakeRagService:
    """Ejecuta busqueda local contra Qdrant con embeddings deterministas."""

    def __init__(self) -> None:
        self._qdrant_repo = QdrantRepository()

    async def search(
        self,
        job_description: str,
        filters: SearchFilters,
        top_k: int,
    ) -> list[CandidateResult]:
        logger.info(
            "rag_search_started",
            mode="local_fake",
            top_k=top_k,
            query_characters=len(job_description),
            filters=filters.model_dump(exclude_none=True),
        )
        try:
            points = await self._qdrant_repo.search_fake(job_description, limit=top_k * 3)
        except Exception as exc:
            raise VectorStoreError(f"No se pudo buscar en Qdrant local: {exc}") from exc

        results: list[CandidateResult] = []
        for point in points:
            payload = point.payload or {}
            if not self._payload_matches_filters(payload, filters):
                continue

            chunk_text = payload.get("chunk_text", "")
            results.append(
                CandidateResult(
                    candidate_id=payload.get("candidate_id", ""),
                    file_name=payload.get("file_name", ""),
                    score=round(point.score or 0.0, 4),
                    summary=self._fake_summary(chunk_text),
                    reason=self._fake_reason(job_description, chunk_text),
                    contact_info=payload.get("contact_info", {}),
                    exp_years=payload.get("exp_years", 0),
                    location=payload.get("location"),
                )
            )
            if len(results) >= top_k:
                break

        logger.info(
            "rag_search_completed",
            mode="local_fake",
            qdrant_points=len(points),
            returned_results=len(results),
        )
        return results

    @staticmethod
    def _payload_matches_filters(payload: dict, filters: SearchFilters) -> bool:
        if filters.min_exp_years is not None:
            exp_years = payload.get("exp_years")
            if exp_years is not None and exp_years < filters.min_exp_years:
                return False

        if filters.location:
            location = payload.get("location")
            if location and filters.location.lower() not in location.lower():
                return False

        return True

    @staticmethod
    def _fake_summary(chunk_text: str) -> str:
        text = " ".join(chunk_text.split())
        if not text:
            return "Resumen local fake no disponible: el fragmento indexado esta vacio."
        return f"Resumen local fake: {text[:220]}"

    @staticmethod
    def _fake_reason(job_description: str, chunk_text: str) -> str:
        query_terms = {term.lower().strip(".,;:()[]") for term in job_description.split()}
        chunk_terms = {term.lower().strip(".,;:()[]") for term in chunk_text.split()}
        overlap = sorted(term for term in query_terms & chunk_terms if len(term) > 3)
        if overlap:
            return "Coincidencia local fake por terminos compartidos: " + ", ".join(overlap[:8])
        return "Coincidencia local fake basada en similitud vectorial determinista."
