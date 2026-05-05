"""Router de búsqueda – rag-service."""
from fastapi import APIRouter, HTTPException, status

from shared.models import ApiResponse
from ..services.rag_service import RagService
from .schemas import CandidateResult, SearchRequest

router = APIRouter()
_svc = RagService()


@router.post("", response_model=ApiResponse)
async def search_candidates(body: SearchRequest) -> ApiResponse:
    """
    Recibe la descripción del puesto y filtros opcionales.
    Devuelve una lista de candidatos ordenados por relevancia con justificación.
    Flujo síncrono: baja latencia, NO dispara procesamiento de documentos.
    """
    try:
        results: list[CandidateResult] = await _svc.search(
            job_description=body.job_description,
            filters=body.filters,
            top_k=body.top_k,
        )
        return ApiResponse(data=results)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
