"""Modelos de datos compartidos entre microservicios."""
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


# Estados posibles durante el ciclo de vida de un documento
class ProcessingStatus(StrEnum):
    PENDING = "pending"      # recibido pero sin procesar
    PROCESSING = "processing" # en curso
    COMPLETED = "completed"  # procesado y guardado en Qdrant
    FAILED = "failed"        # error irrecuperable


# ── Payload Qdrant ────────────────────────────────────────────────────────────


class CandidatePayload(BaseModel):
    """Estructura minima del payload JSON almacenado en Qdrant."""

    candidate_id: str                              # identificador unico del candidato
    file_name: str                                 # nombre original del PDF
    contact_info: dict[str, Any] = Field(default_factory=dict)  # datos de contacto extraidos
    exp_years: int = 0                             # anos de experiencia detectados
    location: str | None = None                    # ubicacion del candidato
    chunk_text: str                                # fragmento de texto del CV


# ── Respuestas estandar API ───────────────────────────────────────────────────


class ApiResponse(BaseModel):
    """Envelope de respuesta estandar para todos los endpoints."""

    # Carga util de la respuesta exitosa
    data: Any | None = None
    # Mensaje de error; None cuando la operacion fue correcta
    error: str | None = None


# ── Documentos ────────────────────────────────────────────────────────────────


class DocumentStatus(BaseModel):
    id: UUID                          # identificador unico del documento
    file_name: str                    # nombre del archivo original
    status: ProcessingStatus          # estado actual del procesamiento
    created_at: datetime              # momento de recepcion
    updated_at: datetime              # ultima actualizacion de estado
    error_detail: str | None = None   # detalle del error si el estado es FAILED
