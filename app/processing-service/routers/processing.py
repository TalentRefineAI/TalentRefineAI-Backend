"""Router de procesamiento – processing-service."""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from shared.models import ApiResponse
from ..services.processing_service import ProcessingService

router = APIRouter()
_svc = ProcessingService()


@router.post("/{doc_id}", status_code=status.HTTP_202_ACCEPTED, response_model=ApiResponse)
async def trigger_processing(
    doc_id: UUID,
    blob_name: str,
    background_tasks: BackgroundTasks,
) -> ApiResponse:
    """
    Encola el procesamiento pesado (extracción + chunking + persistencia en Qdrant).
    Llamado internamente por el ingest-service tras subir el blob.
    """
    background_tasks.add_task(_svc.process_document, doc_id, blob_name)
    return ApiResponse(data={"id": str(doc_id), "status": "queued"})
