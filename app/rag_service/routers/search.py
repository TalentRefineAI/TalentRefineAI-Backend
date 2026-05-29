from fastapi import APIRouter, HTTPException, status

from ..services.rag_engine import RAGEngine
from .schemas import SearchRequest, SearchResponse

router = APIRouter()
# Instancia del motor RAG que orquesta el pipeline de busqueda
_engine = RAGEngine()


@router.post("", response_model=SearchResponse)
async def search_candidates(body: SearchRequest) -> SearchResponse:
    """
    Recibe la descripcion del puesto y filtros opcionales.
    Devuelve candidatos ordenados por relevancia con resumen y justificacion.
    Flujo sincrono de baja latencia; no dispara procesamiento de documentos.
    """
    try:
        return await _engine.search_candidates(body)
    except Exception as exc:
        # Cualquier error inesperado se devuelve como 500 con detalle
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
