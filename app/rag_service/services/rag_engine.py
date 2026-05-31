"""Motor RAG con filtrado nativo de Qdrant via MetadataFilters de LlamaIndex."""
from __future__ import annotations

import json
import re
from typing import Any

import structlog
from llama_index.core import Settings as LlamaSettings, VectorStoreIndex
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.schema import QueryBundle
from llama_index.core.vector_stores import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from shared.config import get_settings
from shared.exceptions import LLMError, VectorStoreError
from shared.fakes import fake_embedding
from ..integrations.azure_openai import get_embed_model, get_llm
from ..repositories.qdrant_repository import QdrantRepository
from ..routers.schemas import CandidateResult, SearchRequest, SearchResponse

logger = structlog.get_logger(__name__)


class RAGEngine:
    """Motor de busqueda semantica con reranking y generacion de justificaciones via LLM."""

    def __init__(self) -> None:
        settings = get_settings()
        self._use_fake = settings.use_fake_integrations
        self._qdrant_repo = QdrantRepository()
        if not self._use_fake:
            LlamaSettings.embed_model = get_embed_model()
            LlamaSettings.llm = get_llm()

    # ------------------------------------------------------------------ #
    # API publica                                                         #
    # ------------------------------------------------------------------ #

    async def search_candidates(self, request: SearchRequest) -> SearchResponse:
        """Punto de entrada: delega a Azure o a la implementacion fake."""
        if self._use_fake:
            return await self._search_fake(request)
        return await self._search_azure(request)

    # ------------------------------------------------------------------ #
    # Implementacion Azure (LlamaIndex + Qdrant + OpenAI)                #
    # ------------------------------------------------------------------ #

    async def _search_azure(self, request: SearchRequest) -> SearchResponse:
        # Guard: coleccion no existente o vacia
        try:
            client = self._qdrant_repo._client
            if not await client.collection_exists(self._qdrant_repo._collection):
                logger.warning("qdrant_collection_missing", collection=self._qdrant_repo._collection)
                return SearchResponse(results=[])
            info = await client.get_collection(self._qdrant_repo._collection)
            if (info.points_count or 0) == 0:
                logger.info("qdrant_collection_empty", collection=self._qdrant_repo._collection)
                return SearchResponse(results=[])
        except Exception as exc:
            logger.warning("qdrant_collection_check_failed", error=str(exc))

        try:
            vector_store = await self._qdrant_repo.get_vector_store()
        except Exception as exc:
            raise VectorStoreError(f"Error obteniendo VectorStore: {exc}") from exc

        index = VectorStoreIndex.from_vector_store(
            vector_store,
            embed_model=LlamaSettings.embed_model,
        )

        metadata_filters = self._build_metadata_filters(request)
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=request.top_k * 3,
            filters=metadata_filters,
        )

        try:
            source_nodes = await retriever.aretrieve(QueryBundle(request.job_description))
        except Exception as exc:
            raise LLMError(f"Error en recuperacion vectorial: {exc}") from exc

        if not source_nodes:
            return SearchResponse(results=[])

        # Agrupar nodos por candidate_id (UUID del payload de Qdrant)
        candidate_nodes: dict[str, list] = {}
        for node in source_nodes:
            cid = node.metadata.get("candidate_id") or node.node_id
            candidate_nodes.setdefault(cid, []).append(node)

        # Mejor score coseno por candidato y file_name
        candidate_scores: dict[str, float] = {}
        candidate_file: dict[str, str] = {}
        for cid, nodes in candidate_nodes.items():
            scores = [n.score for n in nodes if n.score is not None]
            candidate_scores[cid] = max(scores) if scores else 0.0
            candidate_file[cid] = nodes[0].metadata.get("file_name", "")

        # Top-K candidatos por score
        top_cids = sorted(candidate_scores, key=candidate_scores.__getitem__, reverse=True)[: request.top_k]

        # LLM extrae informacion estructurada de todos los candidatos en una sola llamada
        try:
            llm_data = await self._extract_all_candidates(
                {cid: candidate_nodes[cid] for cid in top_cids},
                request.job_description,
            )
        except Exception as exc:
            logger.warning("llm_extraction_failed", error=str(exc))
            llm_data = {}

        results: list[CandidateResult] = []
        for cid in top_cids:
            info = llm_data.get(cid, {})
            results.append(
                CandidateResult(
                    candidate_id=cid,
                    file_name=candidate_file[cid],
                    score=candidate_scores[cid],
                    summary=info.get("summary", ""),
                    justification=info.get("justification", ""),
                    contact_info={
                        "name": info.get("name", ""),
                        "email": info.get("email", ""),
                        "phone": info.get("phone", ""),
                        "skills": info.get("skills", []),
                    },
                    exp_years=int(info.get("exp_years") or 0),
                    location=info.get("location"),
                )
            )

        return SearchResponse(results=results)

    # ------------------------------------------------------------------ #
    # Extraccion estructurada via LLM                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _extract_all_candidates(
        candidate_nodes: dict[str, list],
        job_description: str,
    ) -> dict[str, dict[str, Any]]:
        """
        Llama al LLM una sola vez para extraer informacion estructurada de todos
        los candidatos (nombre, email, telefono, skills, exp_years, ubicacion,
        resumen y justificacion de encaje).
        Devuelve dict keyed por candidate_id (UUID).
        """
        sections: list[str] = []
        for cid, nodes in candidate_nodes.items():
            chunks = "\n\n".join(n.get_content() for n in nodes)
            sections.append(f"[CANDIDATE_ID: {cid}]\n{chunks}")

        context = "\n\n---\n\n".join(sections)

        prompt = (
            "Eres un experto en seleccion de personal IT.\n"
            "A continuacion se muestran fragmentos de CV de uno o varios candidatos, "
            "cada uno identificado por su CANDIDATE_ID exacto.\n\n"
            + context
            + f"\n\nPUESTO A CUBRIR:\n{job_description}\n\n"
            "Para cada candidato extrae y devuelve un array JSON. "
            "Cada objeto del array debe contener EXACTAMENTE estos campos:\n"
            '- "candidate_id": el CANDIDATE_ID exacto que aparece entre corchetes (string, NO lo modifiques)\n'
            '- "name": nombre completo del candidato extraido del CV (string)\n'
            '- "email": correo electronico (string o null si no aparece)\n'
            '- "phone": numero de telefono (string o null si no aparece)\n'
            '- "exp_years": anos de experiencia profesional estimados por las fechas del CV (entero)\n'
            '- "location": ciudad o pais del candidato (string o null si no aparece)\n'
            '- "skills": lista de habilidades tecnicas principales, maximo 10 (array de strings)\n'
            '- "summary": resumen profesional del candidato en 2-3 frases (string)\n'
            '- "justification": motivo de encaje con el puesto en 2-3 frases (string)\n\n'
            "Devuelve SOLO el array JSON valido, sin markdown ni texto adicional."
        )

        llm = LlamaSettings.llm
        response = await llm.acomplete(prompt)
        response_text = str(response)

        logger.info("llm_extraction_response", length=len(response_text))

        try:
            match = re.search(r"\[.*\]", response_text, re.DOTALL)
            if not match:
                raise ValueError("No JSON array found in LLM response")
            items: list[dict] = json.loads(match.group())
            return {item["candidate_id"]: item for item in items if "candidate_id" in item}
        except Exception as exc:
            logger.warning("llm_extraction_parse_failed", error=str(exc), response=response_text[:300])
            return {}

    # ------------------------------------------------------------------ #
    # Implementacion fake (sin Azure)                                     #
    # ------------------------------------------------------------------ #

    async def _search_fake(self, request: SearchRequest) -> SearchResponse:
        raw_points = await self._qdrant_repo.search_fake(
            query_text=request.job_description,
            limit=request.top_k * 3,
        )

        results: list[CandidateResult] = []
        for point in raw_points:
            payload = point.payload or {}
            if not self._payload_matches_filters(payload, request):
                continue
            chunk_text = payload.get("chunk_text", "")
            results.append(
                CandidateResult(
                    candidate_id=payload.get("candidate_id", str(point.id)),
                    file_name=payload.get("file_name", ""),
                    score=float(point.score) if hasattr(point, "score") else 0.0,
                    summary=self._fake_summary(chunk_text),
                    justification=self._fake_justification(request.job_description, chunk_text),
                    contact_info=payload.get("contact_info", {}),
                    exp_years=int(payload.get("exp_years", 0)),
                    location=payload.get("location"),
                )
            )
            if len(results) >= request.top_k:
                break

        return SearchResponse(results=results)

    # ------------------------------------------------------------------ #
    # Helpers estaticos                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_metadata_filters(request: SearchRequest) -> MetadataFilters | None:
        """Construye filtros nativos de Qdrant via LlamaIndex MetadataFilters."""
        filters: list[MetadataFilter] = []
        if request.min_exp_years is not None:
            filters.append(
                MetadataFilter(
                    key="exp_years",
                    operator=FilterOperator.GTE,
                    value=request.min_exp_years,
                )
            )
        if request.required_location:
            filters.append(
                MetadataFilter(
                    key="location",
                    operator=FilterOperator.EQ,
                    value=request.required_location,
                )
            )
        return MetadataFilters(filters=filters) if filters else None

    @staticmethod
    def _payload_matches_filters(payload: dict, request: SearchRequest) -> bool:
        """Filtra Python-side en modo fake (Qdrant local no aplica los filtros)."""
        if request.min_exp_years is not None:
            if int(payload.get("exp_years", 0)) < request.min_exp_years:
                return False
        if request.required_location:
            loc = payload.get("location", "") or ""
            if loc.lower() != request.required_location.lower():
                return False
        return True

    @staticmethod
    def _fake_summary(chunk_text: str) -> str:
        words = chunk_text.split()
        preview = " ".join(words[:30])
        return f"[FAKE] {preview}..." if len(words) > 30 else f"[FAKE] {chunk_text}"

    @staticmethod
    def _fake_justification(job_description: str, chunk_text: str) -> str:
        keywords = job_description.split()[:5]
        return f"[FAKE] Candidato evaluado para: {chr(39).join(keywords)}. Fragmento: {chunk_text[:80]}"

    async def list_candidates(self) -> list[dict]:
        """Devuelve todos los candidatos únicos almacenados en Qdrant."""
        return await self._qdrant_repo.list_candidates()

    async def delete_all_candidates(self) -> None:
        """Elimina todos los candidatos del vector store."""
        await self._qdrant_repo.delete_all()
