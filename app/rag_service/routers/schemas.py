"""Modelos de request/response del endpoint /search."""
from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    """Filtros opcionales aplicados antes de la busqueda vectorial."""

    # Filtra candidatos con menos anos de experiencia que el valor indicado
    min_exp_years: int | None = Field(None, ge=0)
    # Filtra por ubicacion; la comparacion es case-insensitive y por contenido
    location: str | None = None


class SearchRequest(BaseModel):
    """Payload recibido en POST /search."""

    # Descripcion del puesto que el reclutador quiere cubrir
    job_description: str = Field(..., min_length=10, description="Descripcion del puesto")
    # Filtros opcionales; si no se envian se usan los valores por defecto
    filters: SearchFilters = Field(default_factory=SearchFilters)
    # Numero de candidatos a devolver; entre 1 y 20
    top_k: int = Field(5, ge=1, le=20)


class CandidateResult(BaseModel):
    """Resultado individual de busqueda con score y justificacion generada por el LLM."""

    candidate_id: str   # identificador unico del candidato en Qdrant
    file_name: str      # nombre del CV original
    score: float        # similitud coseno normalizada entre 0 y 1
    summary: str        # resumen del candidato generado por GPT-4
    reason: str         # motivo por el que encaja con el puesto
    contact_info: dict  # datos de contacto extraidos del CV
    exp_years: int      # anos de experiencia detectados
    location: str | None
