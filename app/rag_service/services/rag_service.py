"""
Orquestación del flujo RAG completo implementado con LlamaIndex.
Pasos:
  1. Embedding de la query con Azure OpenAI
  2. Prefiltrado por metadatos en Qdrant
  3. Búsqueda vectorial
  4. Construcción de prompt y llamada a GPT-4
  5. Respuesta justificada
"""
import structlog
from llama_index.core import VectorStoreIndex
from llama_index.core.retrievers import VectorIndexRetriever
from shared.exceptions import LLMError, VectorStoreError

from ..integrations.azure_openai import get_embed_model, get_llm
from ..repositories.qdrant_repository import QdrantRepository
from ..routers.schemas import CandidateResult, SearchFilters

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """Eres un asistente de reclutamiento.
Dado un conjunto de fragmentos de CV y una descripción de puesto,
devuelve para cada candidato un resumen conciso y el motivo por el que encaja (o no).
Responde siempre en español."""


class RagService:
    def __init__(self) -> None:
        self._qdrant_repo = QdrantRepository()
        # Modelo de embedding compartido con el processing-service
        self._embed_model = get_embed_model()
        # Modelo de chat (GPT-4) para generar resumen y justificacion
        self._llm = get_llm()

    async def search(
        self,
        job_description: str,
        filters: SearchFilters,
        top_k: int,
    ) -> list[CandidateResult]:
        """
        Ejecuta el pipeline RAG y devuelve candidatos con score y justificacion.
        Flujo sincrono de baja latencia.
        """
        logger.info(
            "rag_search_started",
            mode="azure",
            top_k=top_k,
            query_characters=len(job_description),
            filters=filters.model_dump(exclude_none=True),
        )

        # 1. Obtener el vector store y construir el indice de LlamaIndex
        try:
            vector_store = await self._qdrant_repo.get_vector_store()
        except Exception as exc:
            raise VectorStoreError(f"No se pudo conectar con Qdrant: {exc}") from exc

        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            embed_model=self._embed_model,
        )

        # 2. Recuperar mas chunks de los necesarios para poder aplicar el filtro
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=top_k * 3,  # se recuperan el triple para tener margen de filtrado
        )
        nodes = await retriever.aretrieve(job_description)

        # 3. Filtrar en Python hasta tener filtros payload nativos configurados en Qdrant
        nodes = self._apply_filters(nodes, filters)[:top_k]

        if not nodes:
            return []

        # 4. Generar resumen y justificacion con GPT-4
        try:
            results = await self._generate_results(job_description, nodes)
        except Exception as exc:
            raise LLMError(f"Error en llamada al LLM: {exc}") from exc

        logger.info("rag_search_completed", mode="azure", returned_results=len(results))
        return results

    @staticmethod
    def _apply_filters(nodes, filters: SearchFilters):
        filtered = []
        for node in nodes:
            payload = node.metadata
            # Descarta candidatos con menos experiencia de la requerida
            if filters.min_exp_years is not None:
                exp_years = payload.get("exp_years")
                if exp_years is not None and exp_years < filters.min_exp_years:
                    continue

            if filters.location:
                location = payload.get("location")
                if location and filters.location.lower() not in location.lower():
                    continue
            filtered.append(node)
        return filtered

    async def _generate_results(self, job_description: str, nodes) -> list[CandidateResult]:
        """Llama a GPT-4 para generar resumen y motivo de encaje por cada candidato."""
        results = []
        for node in nodes:
            payload = node.metadata
            chunk_text = node.get_content()

            # Construye el prompt incluyendo la descripcion del puesto y el fragmento del CV
            candidate_id = payload.get("candidate_id", "")
            prompt = (
                f"Descripcion del puesto:\n{job_description}\n\n"
                f"Fragmento del CV del candidato {candidate_id}:\n{chunk_text}\n\n"
                "Responde en JSON con los campos: summary (str), reason (str)."
            )

            response = await self._llm.acomplete(f"{_SYSTEM_PROMPT}\n\n{prompt}")
            import json
            import re
            # Extrae el bloque JSON de la respuesta del modelo
            match = re.search(r"\{.*\}", response.text, re.DOTALL)
            parsed = json.loads(match.group()) if match else {"summary": "", "reason": ""}

            results.append(
                CandidateResult(
                    candidate_id=payload.get("candidate_id", ""),
                    file_name=payload.get("file_name", ""),
                    score=round(node.score or 0.0, 4),  # redondea a 4 decimales
                    summary=parsed.get("summary", ""),
                    reason=parsed.get("reason", ""),
                    contact_info=payload.get("contact_info", {}),
                    exp_years=payload.get("exp_years", 0),
                    location=payload.get("location"),
                )
            )
        return results
