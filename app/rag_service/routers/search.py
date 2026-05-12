"""Router de búsqueda – rag-service."""
from fastapi import APIRouter, HTTPException, status
from shared.models import ApiResponse

from ..services.rag_service import RagService
from .schemas import CandidateResult, SearchRequest

router = APIRouter()
# Instancia del servicio RAG que orquesta el pipeline de busqueda
_svc = RagService()


@router.post("", response_model=ApiResponse)
async def search_candidates(body: SearchRequest) -> ApiResponse:
    """
    Recibe la descripcion del puesto y filtros opcionales.
    Devuelve candidatos ordenados por relevancia con resumen y justificacion.
    Flujo sincrono de baja latencia; no dispara procesamiento de documentos.
    """
    try:
        results: list[CandidateResult] = await _svc.search(
            job_description=body.job_description,
            filters=body.filters,
            top_k=body.top_k,
        )
        return ApiResponse(data=results)
    except Exception as exc:
        # Cualquier error inesperado se devuelve como 500 con detalle
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
