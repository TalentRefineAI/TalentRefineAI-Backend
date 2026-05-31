from fastapi import APIRouter, HTTPException, status

from ..services.rag_engine import RAGEngine
from .schemas import CandidateListItem, CandidateListResponse, SearchRequest, SearchResponse

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

@router.get("/candidates", response_model=CandidateListResponse)
async def list_candidates() -> CandidateListResponse:
    """
    Devuelve todos los candidatos indexados en Qdrant (sin llamadas a LLM).
    Usado por el frontend para mostrar el listado de candidatos disponibles.
    """
    try:
        items = await _engine.list_candidates()
        return CandidateListResponse(
            total=len(items),
            candidates=[CandidateListItem(**i) for i in items],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.delete("/candidates")
async def delete_all_candidates() -> dict:
    """
    Elimina toda la colección de candidatos de Qdrant.
    Usado por el frontend para el botón "Eliminar todo".
    """
    try:
        await _engine.delete_all_candidates()
        return {"deleted": True}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
