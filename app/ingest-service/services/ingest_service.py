"""Lógica de negocio de ingesta – ingest-service."""
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import BackgroundTasks, UploadFile

from shared.exceptions import StorageError
from shared.models import DocumentStatus, ProcessingStatus
from ..repositories.blob_repository import BlobRepository

logger = structlog.get_logger(__name__)

_ALLOWED_CONTENT_TYPES = {"application/pdf"}


class IngestService:
    def __init__(self) -> None:
        self._blob = BlobRepository()
        # En producción este estado vivirá en una base de datos o cola (Azure Storage Queue)
        self._status_store: dict[uuid.UUID, DocumentStatus] = {}

    async def accept_uploads(
        self,
        files: list[UploadFile],
        background_tasks: BackgroundTasks,
    ) -> list[dict]:
        results = []
        for file in files:
            if file.content_type not in _ALLOWED_CONTENT_TYPES:
                logger.warning("rejected_file", filename=file.filename, content_type=file.content_type)
                results.append({"file_name": file.filename, "error": "Solo se admiten archivos PDF"})
                continue

            doc_id = uuid.uuid4()
            now = datetime.now(timezone.utc)
            status = DocumentStatus(
                id=doc_id,
                file_name=file.filename or "unknown.pdf",
                status=ProcessingStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
            self._status_store[doc_id] = status

            background_tasks.add_task(self._process_upload, doc_id, file)
            results.append({"id": str(doc_id), "file_name": file.filename, "status": ProcessingStatus.PENDING})

        return results

    async def get_status(self, doc_id: uuid.UUID) -> DocumentStatus:
        if doc_id not in self._status_store:
            raise KeyError(doc_id)
        return self._status_store[doc_id]

    async def _process_upload(self, doc_id: uuid.UUID, file: UploadFile) -> None:
        """Tarea de background: sube el PDF a Blob Storage."""
        record = self._status_store[doc_id]
        record.status = ProcessingStatus.PROCESSING
        record.updated_at = datetime.now(timezone.utc)

        try:
            content = await file.read()
            blob_name = f"{doc_id}/{file.filename}"
            await self._blob.upload(blob_name, content, content_type="application/pdf")

            record.status = ProcessingStatus.COMPLETED
            logger.info("blob_uploaded", doc_id=str(doc_id), blob=blob_name)
        except Exception as exc:
            record.status = ProcessingStatus.FAILED
            record.error_detail = str(exc)
            logger.error("upload_failed", doc_id=str(doc_id), error=str(exc))
            raise StorageError(str(exc)) from exc
        finally:
            record.updated_at = datetime.now(timezone.utc)
