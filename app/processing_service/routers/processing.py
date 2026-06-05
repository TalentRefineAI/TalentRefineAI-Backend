"""Router de procesamiento – processing-service."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from shared.exceptions import ProcessingError, VectorStoreError
from shared.models import ApiResponse
from ..services.indexing_service import IndexingService

router = APIRouter()
# Instancia del servicio que orquesta el pipeline de procesamiento
_svc = IndexingService()


@router.post("/{doc_id}", response_model=ApiResponse)
async def trigger_processing(
    doc_id: UUID,
    blob_name: str,       # ruta del blob en Azure Storage
) -> ApiResponse:
    """
    Procesa el CV de forma SÍNCRONA (extraccion + chunking + embeddings + Qdrant).
    Llamado internamente por el ingest-service tras subir el blob.

    Síncrono a propósito: mantiene la réplica viva durante todo el trabajo (la request
    inbound impide que KEDA la escale a cero y mate el procesamiento) y, como el ingest
    dispara secuencialmente, serializa la carga sin saturar la cuota de Azure OpenAI.
    """
    try:
        await _svc.process_document(doc_id, blob_name)
        return ApiResponse(data={"id": str(doc_id), "status": "completed"})
    except (ProcessingError, VectorStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
