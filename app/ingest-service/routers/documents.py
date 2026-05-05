"""Router de documentos – ingest-service."""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, status

from shared.exceptions import StorageError
from shared.models import ApiResponse, DocumentStatus
from ..services.ingest_service import IngestService

router = APIRouter()
_svc = IngestService()


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=ApiResponse)
async def upload_documents(
    files: list[UploadFile],
    background_tasks: BackgroundTasks,
) -> ApiResponse:
    """
    Acepta uno o varios PDFs, los sube a Blob y encola el procesamiento.
    Responde rápido (202) sin esperar al procesamiento pesado.
    """
    try:
        result = await _svc.accept_uploads(files, background_tasks)
        return ApiResponse(data=result)
    except StorageError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{doc_id}/status", response_model=ApiResponse)
async def get_document_status(doc_id: UUID) -> ApiResponse:
    """Devuelve el estado de procesamiento de un documento."""
    try:
        doc_status: DocumentStatus = await _svc.get_status(doc_id)
        return ApiResponse(data=doc_status)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")
