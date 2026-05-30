"""Router de upload – ingest-service. [BK-02]"""
from uuid import UUID

import io
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from shared.exceptions import StorageError
from shared.models import ApiResponse, DocumentStatus

from ..services.ingest_service import IngestService

router = APIRouter()
# Instancia del servicio de ingesta; gestiona la logica de negocio
_svc = IngestService()


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=ApiResponse)
async def upload_pdf(
    files: list[UploadFile],
    background_tasks: BackgroundTasks,
) -> ApiResponse:
    """
    [BK-02] Endpoint POST /upload.
    Acepta uno o varios PDFs, los sube a Azure Blob y encola el procesamiento.
    Responde rapido (202) sin bloquear al cliente.
    """
    try:
        result = await _svc.accept_uploads(files, background_tasks)
        return ApiResponse(data=result)
    except StorageError as exc:
        # Error al comunicarse con Blob Storage
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{doc_id}/status", response_model=ApiResponse)
async def get_upload_status(doc_id: UUID) -> ApiResponse:
    """Devuelve el estado actual de procesamiento de un documento subido."""
    try:
        doc_status: DocumentStatus = await _svc.get_status(doc_id)
        return ApiResponse(data=doc_status)
    except KeyError:
        # El identificador no existe en el registro de documentos
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")

@router.get("/{doc_id}/download")
async def download_pdf(doc_id: UUID, filename: str = Query(..., description="Nombre del fichero PDF")) -> StreamingResponse:
    """Descarga el PDF original de un candidato desde Blob Storage."""
    try:
        data = await _svc.download_document(doc_id, filename)
        safe_name = filename.replace(""", "").replace("\", "")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename="{safe_name}""},
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado") from exc
