"""Lógica de negocio de ingesta – ingest-service."""
import uuid
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import BackgroundTasks, UploadFile
from shared.exceptions import StorageError
from shared.models import DocumentStatus, ProcessingStatus

from ..repositories.blob_repository import BlobRepository

logger = structlog.get_logger(__name__)

_ALLOWED_CONTENT_TYPES = {"application/pdf"}


class IngestService:
    def __init__(self) -> None:
        # BlobRepository se inicializa de forma lazy en la primera llamada real
        # para evitar conectar con Azure al arrancar el servicio
        self._blob: BlobRepository | None = None
        # En produccion este estado vivira en una base de datos o cola (Azure Storage Queue)
        self._status_store: dict[uuid.UUID, DocumentStatus] = {}

    def _get_blob(self) -> BlobRepository:
        # Crea el cliente Blob solo cuando se necesita por primera vez
        if self._blob is None:
            self._blob = BlobRepository()
        return self._blob

    async def accept_uploads(
        self,
        files: list[UploadFile],
        background_tasks: BackgroundTasks,
    ) -> list[dict]:
        results = []
        for file in files:
            # Rechaza cualquier archivo que no sea PDF
            if file.content_type not in _ALLOWED_CONTENT_TYPES:
                logger.warning(
                    "rejected_file",
                    filename=file.filename,
                    content_type=file.content_type,
                )
                results.append(
                    {
                        "file_name": file.filename,
                        "error": "Solo se admiten archivos PDF",
                    }
                )
                continue

            # Genera un identificador unico para rastrear este documento
            doc_id = uuid.uuid4()
            now = datetime.now(UTC)
            status = DocumentStatus(
                id=doc_id,
                file_name=file.filename or "unknown.pdf",
                status=ProcessingStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
            self._status_store[doc_id] = status

            # Delega la subida real a Blob como tarea en segundo plano
            background_tasks.add_task(self._process_upload, doc_id, file)
            logger.info(
                "document_upload_accepted",
                doc_id=str(doc_id),
                file_name=file.filename,
                content_type=file.content_type,
            )
            results.append(
                {
                    "id": str(doc_id),
                    "file_name": file.filename,
                    "status": ProcessingStatus.PENDING,
                }
            )

        return results

    async def get_status(self, doc_id: uuid.UUID) -> DocumentStatus:
        # Lanza KeyError si el doc_id no existe; el router lo convierte en 404
        if doc_id not in self._status_store:
            raise KeyError(doc_id)
        return self._status_store[doc_id]

    async def _process_upload(self, doc_id: uuid.UUID, file: UploadFile) -> None:
        """Tarea de background: sube el PDF a Blob Storage y dispara processing."""
        record = self._status_store[doc_id]
        # Marca el documento como en procesamiento antes de empezar la subida
        record.status = ProcessingStatus.PROCESSING
        record.updated_at = datetime.now(UTC)

        try:
            content = await file.read()
            # La ruta en Blob incluye el doc_id para evitar colisiones de nombre
            blob_name = f"{doc_id}/{file.filename}"
            await self._get_blob().upload(blob_name, content, content_type="application/pdf")

            await self._trigger_processing(doc_id, blob_name)

            # Se mantiene en processing hasta tener un estado compartido entre servicios.
            record.status = ProcessingStatus.PROCESSING
            logger.info(
                "blob_uploaded_and_processing_triggered",
                doc_id=str(doc_id),
                blob_name=blob_name,
            )
        except Exception as exc:
            # Registra el error y actualiza el estado para que sea consultable
            record.status = ProcessingStatus.FAILED
            record.error_detail = str(exc)
            logger.error("upload_failed", doc_id=str(doc_id), error=str(exc))
            raise StorageError(str(exc)) from exc
        finally:
            # El timestamp se actualiza siempre, tanto en exito como en fallo
            record.updated_at = datetime.now(UTC)

    async def _trigger_processing(self, doc_id: uuid.UUID, blob_name: str) -> None:
        """Dispara el procesamiento llamando al processing-service."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"http://processing-service:8000/processing/{doc_id}",
                    params={"blob_name": blob_name},
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info(
                    "processing_triggered",
                    doc_id=str(doc_id),
                    blob_name=blob_name,
                    status_code=response.status_code,
                )
            except httpx.HTTPError as exc:
                logger.error("processing_trigger_failed", doc_id=str(doc_id), error=str(exc))
                raise
