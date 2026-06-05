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
async def download_pdf(
    doc_id: UUID,
    filename: str = Query(..., description="Nombre del fichero PDF"),
) -> StreamingResponse:
    """Descarga el PDF original de un candidato desde Blob Storage."""
    # Sanitize: reject filenames with path-traversal sequences or separators
    import os
    clean_filename = os.path.basename(filename.replace("\\", "/"))
    if not clean_filename or ".." in clean_filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nombre de fichero no valido"
        )
    try:
        data = await _svc.download_document(doc_id, clean_filename)
        safe_name = clean_filename.replace('"', "")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado"
        ) from exc


@router.delete("/all")
async def delete_all_uploads() -> dict:
    """
    Elimina todos los documentos de Blob Storage y limpia el estado en memoria.
    Usado por el frontend para el botón "Eliminar todo".
    """
    try:
        await _svc.delete_all_documents()
        return {"deleted": True}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
