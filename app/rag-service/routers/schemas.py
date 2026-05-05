"""Modelos de request/response del endpoint /search."""
from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    """Filtros opcionales de prefiltrado en Qdrant."""

    min_exp_years: int | None = Field(None, ge=0)
    location: str | None = None


class SearchRequest(BaseModel):
    """Payload recibido por POST /search."""

    job_description: str = Field(..., min_length=10, description="Descripción del puesto")
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(5, ge=1, le=20)


class CandidateResult(BaseModel):
    """Resultado individual de búsqueda."""

    candidate_id: str
    file_name: str
    score: float
    summary: str
    reason: str
    contact_info: dict
    exp_years: int
    location: str | None
