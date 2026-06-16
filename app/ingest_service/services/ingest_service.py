"""Lógica de negocio de ingesta – ingest-service."""
import hashlib
import time
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


def _log_success(log, event: str, **fields) -> None:
    """Éxito final de negocio que debe sobrevivir a LOG_LEVEL=SUCCESS/WARNING en Azure.

    PLACEHOLDER (dependencia Integrante 2): mientras shared/logging.py no registre el nivel
    custom SUCCESS=25 ni exponga logger.success(), se emite a INFO. Cuando Integrante 2
    aterrice el nivel, este helper usará logger.success() automáticamente. Se puede entonces
    sustituir por la llamada directa log.success(event, **fields) y borrar este helper.
    """
    success = getattr(log, "success", None)
    (success if callable(success) else log.info)(event, **fields)


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
        start = time.perf_counter()
        record = self._status_store[doc_id]
        record.status = ProcessingStatus.PROCESSING
        record.updated_at = datetime.now(timezone.utc)
        # La ruta en Blob incluye el doc_id para evitar colisiones de nombre
        blob_name = f"{doc_id}/{filename}"

        # Subida a Blob: los errores se atribuyen como upload_failed
        try:
            await self._blob.upload(blob_name, content, content_type="application/pdf")
        except Exception as exc:
            record.status = ProcessingStatus.FAILED
            record.error_detail = str(exc)
            record.updated_at = datetime.now(timezone.utc)
            logger.error(
                "upload_failed",
                doc_id=str(doc_id),
                blob=blob_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise StorageError(str(exc)) from exc

        # Disparo del processing-service: el evento de error (processing_trigger_failed)
        # lo emite _trigger_processing; aquí solo actualizamos estado y propagamos.
        try:
            await self._trigger_processing(doc_id, blob_name)
        except Exception as exc:
            record.status = ProcessingStatus.FAILED
            record.error_detail = str(exc)
            record.updated_at = datetime.now(timezone.utc)
            raise StorageError(str(exc)) from exc

        record.status = ProcessingStatus.COMPLETED
        record.updated_at = datetime.now(timezone.utc)
        # Éxito final del flujo de ingesta: PDF en Blob + processing disparado correctamente.
        _log_success(
            logger,
            "document_upload_completed",
            doc_id=str(doc_id),
            file_name=filename,
            file_size_bytes=len(content),
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    async def _trigger_processing(self, doc_id: uuid.UUID, blob_name: str) -> None:
        """Llama al processing-service via HTTP usando la URL configurada en PROCESSING_SERVICE_URL."""
        base_url = get_settings().processing_service_url
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{base_url}/processing/{doc_id}",
                    params={"blob_name": blob_name},
                    # El processing es síncrono (indexa antes de responder); damos margen
                    # amplio para CV grandes + extracción LLM + embeddings.
                    timeout=150.0,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error(
                    "processing_trigger_failed",
                    doc_id=str(doc_id),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise

    async def delete_all_documents(self) -> None:
        """Elimina todos los documentos del Blob Storage y limpia el estado en memoria."""
        await self._blob.delete_all()
        self._status_store.clear()
        logger.info("all_documents_deleted")
