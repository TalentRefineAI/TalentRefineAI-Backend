"""Lógica de negocio de ingesta – ingest-service."""
import hashlib
import uuid
from datetime import datetime, timezone

import httpx
import structlog
from fastapi import BackgroundTasks, UploadFile

from shared.config import get_settings
from shared.exceptions import StorageError
from shared.models import DocumentStatus, ProcessingStatus
from ..repositories.blob_repository import BlobRepository

logger = structlog.get_logger(__name__)

_ALLOWED_CONTENT_TYPES = {"application/pdf"}


class IngestService:
    def __init__(self) -> None:
        self._blob = BlobRepository()
        # En produccion este estado vivira en una base de datos o cola (Azure Storage Queue)
        self._status_store: dict[uuid.UUID, DocumentStatus] = {}

    async def accept_uploads(
        self,
        files: list[UploadFile],
        background_tasks: BackgroundTasks,
    ) -> list[dict]:
        results = []
        for file in files:
            # Rechaza cualquier archivo que no sea PDF
            if file.content_type not in _ALLOWED_CONTENT_TYPES:
                logger.warning("rejected_file", filename=file.filename, content_type=file.content_type)
                results.append({"file_name": file.filename, "error": "Solo se admiten archivos PDF"})
                continue

            # Lee el contenido para generar UUID deterministico (evita duplicados del mismo PDF)
            content_bytes = await file.read()
            sha256 = hashlib.sha256(content_bytes).hexdigest()
            doc_id = uuid.uuid5(uuid.NAMESPACE_URL, sha256)
            now = datetime.now(timezone.utc)
            status = DocumentStatus(
                id=doc_id,
                file_name=file.filename or "unknown.pdf",
                status=ProcessingStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
            self._status_store[doc_id] = status

            # Delega la subida real a Blob como tarea en segundo plano
            background_tasks.add_task(self._process_upload, doc_id, content_bytes, file.filename or "unknown.pdf")
            results.append({"id": str(doc_id), "file_name": file.filename, "status": ProcessingStatus.PENDING})

        return results

    async def get_status(self, doc_id: uuid.UUID) -> DocumentStatus:
        # Lanza KeyError si el doc_id no existe; el router lo convierte en 404
        if doc_id not in self._status_store:
            raise KeyError(doc_id)
        return self._status_store[doc_id]

    async def download_document(self, doc_id: uuid.UUID, filename: str) -> bytes:
        """Descarga el PDF de un documento desde Blob Storage."""
        blob_name = f"{doc_id}/{filename}"
        return await self._blob.download(blob_name)


    async def _process_upload(self, doc_id: uuid.UUID, content: bytes, filename: str) -> None:
        """Tarea de background: sube el PDF a Blob Storage y dispara el processing-service."""
        record = self._status_store[doc_id]
        record.status = ProcessingStatus.PROCESSING
        record.updated_at = datetime.now(timezone.utc)

        try:
            # La ruta en Blob incluye el doc_id para evitar colisiones de nombre
            blob_name = f"{doc_id}/{filename}"
            await self._blob.upload(blob_name, content, content_type="application/pdf")
            logger.info("blob_uploaded", doc_id=str(doc_id), blob=blob_name)

            # Notifica al processing-service para que procese el PDF subido
            await self._trigger_processing(doc_id, blob_name)

            record.status = ProcessingStatus.COMPLETED
        except Exception as exc:
            record.status = ProcessingStatus.FAILED
            record.error_detail = str(exc)
            logger.error("upload_failed", doc_id=str(doc_id), error=str(exc))
            raise StorageError(str(exc)) from exc
        finally:
            record.updated_at = datetime.now(timezone.utc)

    async def _trigger_processing(self, doc_id: uuid.UUID, blob_name: str) -> None:
        """Llama al processing-service via HTTP usando la URL configurada en PROCESSING_SERVICE_URL."""
        base_url = get_settings().processing_service_url
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{base_url}/processing/{doc_id}",
                    params={"blob_name": blob_name},
                    timeout=30.0,
                )
                response.raise_for_status()
                logger.info("processing_triggered", doc_id=str(doc_id), blob_name=blob_name)
            except httpx.HTTPError as exc:
                logger.error("processing_trigger_failed", doc_id=str(doc_id), error=str(exc))
                raise

    async def delete_all_documents(self) -> None:
        """Elimina todos los documentos del Blob Storage y limpia el estado en memoria."""
        await self._blob.delete_all()
        self._status_store.clear()
        logger.info("all_documents_deleted")
