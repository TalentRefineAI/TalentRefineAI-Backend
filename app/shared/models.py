"""Modelos de datos compartidos entre microservicios."""
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Payload Qdrant ────────────────────────────────────────────────────────────


class CandidatePayload(BaseModel):
    """Estructura mínima del payload JSON almacenado en Qdrant."""

    candidate_id: str
    file_name: str
    contact_info: dict[str, Any] = Field(default_factory=dict)
    exp_years: int = 0
    location: str | None = None
    chunk_text: str


# ── Respuestas estándar API ───────────────────────────────────────────────────


class ApiResponse(BaseModel):
    """Envelope de respuesta estándar para todos los endpoints."""

    data: Any | None = None
    error: str | None = None


# ── Documentos ────────────────────────────────────────────────────────────────


class DocumentStatus(BaseModel):
    id: UUID
    file_name: str
    status: ProcessingStatus
    created_at: datetime
    updated_at: datetime
    error_detail: str | None = None
