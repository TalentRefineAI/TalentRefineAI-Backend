"""Modelos de request/response del endpoint /search."""
from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Payload recibido en POST /search."""

    job_description: str = Field(..., min_length=10, description="Descripcion del puesto a cubrir")
    min_exp_years: float | None = Field(None, ge=0, description="Minimo de anos de experiencia requeridos")
    required_location: str | None = Field(None, description="Ubicacion requerida; comparacion exacta en Qdrant")
    top_k: int = Field(5, ge=1, le=20, description="Numero de candidatos a devolver")


class CandidateResult(BaseModel):
    """Resultado individual de busqueda con score y justificacion generada por el LLM."""

    candidate_id: str
    file_name: str
    score: float                   # similitud coseno entre 0 y 1
    summary: str                   # resumen conciso del candidato generado por GPT
    justification: str             # motivo de encaje con el puesto generado por GPT
    contact_info: dict[str, Any]
    exp_years: int
    location: str | None


class SearchResponse(BaseModel):
    """Respuesta del endpoint POST /search."""

    results: list[CandidateResult]
    processing_time_ms: int = 0


class CandidateListItem(BaseModel):
    """Candidato resumido para el listado de disponibles."""

    candidate_id: str
    name: str
    email: str
    file_name: str


class CandidateListResponse(BaseModel):
    """Respuesta del endpoint GET /search/candidates."""

    total: int
    candidates: list[CandidateListItem]
    processing_time_ms: int = 0
