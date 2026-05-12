"""Router de procesamiento – processing-service."""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, status
from shared.config import get_settings
from shared.models import ApiResponse

from ..services.fake_processing_service import FakeProcessingService
from ..services.processing_service import ProcessingService

router = APIRouter()
settings = get_settings()
# Instancia del servicio que orquesta el pipeline de procesamiento.
_svc = FakeProcessingService() if settings.use_fake_integrations else ProcessingService()


@router.post("/{doc_id}", status_code=status.HTTP_202_ACCEPTED, response_model=ApiResponse)
async def trigger_processing(
    doc_id: UUID,
    blob_name: str,       # ruta del blob en Azure Storage
    background_tasks: BackgroundTasks,
) -> ApiResponse:
    """
    Encola el procesamiento pesado (extraccion + chunking + persistencia en Qdrant).
    Llamado internamente por el ingest-service tras subir el blob.
    """
    # El trabajo pesado se ejecuta en segundo plano; la respuesta es inmediata
    background_tasks.add_task(_svc.process_document, doc_id, blob_name)
    return ApiResponse(data={"id": str(doc_id), "status": "queued"})
