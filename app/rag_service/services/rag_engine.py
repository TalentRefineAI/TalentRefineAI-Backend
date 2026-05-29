"""Motor RAG con filtrado nativo de Qdrant via MetadataFilters de LlamaIndex."""
from __future__ import annotations

import json
import re
from typing import Any

import structlog
from llama_index.core import Settings as LlamaSettings, VectorStoreIndex
from llama_index.core.prompts import PromptTemplate
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.response_synthesizers import get_response_synthesizer
from llama_index.core.retrievers import VectorIndexRetriever
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

_SYSTEM_PROMPT = (
    "Eres un experto en seleccion de personal IT. "
    "Evalua a los candidatos basandote UNICAMENTE en los fragmentos de CV recuperados. "
    "Para cada candidato devuelve un objeto JSON con los campos: "
    "candidate_id (str), summary (str resumen conciso), justification (str motivo de encaje). "
    "Devuelve SOLO un array JSON valido, sin texto adicional."
)

_QA_PROMPT_TMPL = (
    _SYSTEM_PROMPT
    + """

Fragmentos de CV recuperados:
---------------------
{context_str}
---------------------

Puesto a cubrir: {query_str}

Respuesta JSON: """
)


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
        # Return empty results if collection does not exist or has no vectors yet
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

        qa_prompt = PromptTemplate(_QA_PROMPT_TMPL)
        response_synthesizer = get_response_synthesizer(
            llm=LlamaSettings.llm,
            text_qa_template=qa_prompt,
        )

        query_engine = RetrieverQueryEngine(
            retriever=retriever,
            response_synthesizer=response_synthesizer,
        )

        try:
            response = await query_engine.aquery(request.job_description)
        except Exception as exc:
            raise LLMError(f"Error en la consulta LLM: {exc}") from exc

        results = self._parse_llm_response(
            str(response.response),
            list(response.source_nodes),
        )
        return SearchResponse(results=results[: request.top_k])

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
    def _parse_llm_response(
        response_text: str,
        source_nodes: list,
    ) -> list[CandidateResult]:
        """
        Parsea la respuesta JSON del LLM y la enriquece con datos del payload de Qdrant.
        Fallback: construye resultados directamente desde source_nodes si el JSON es invalido.
        """
        # Mapeo candidate_id -> (score, payload) desde source_nodes
        node_map: dict[str, tuple[float, dict[str, Any]]] = {}
        for node in source_nodes:
            payload = node.metadata or {}
            cid = payload.get("candidate_id", node.node_id)
            score = float(node.score) if node.score is not None else 0.0
            if cid not in node_map or score > node_map[cid][0]:
                node_map[cid] = (score, payload)

        # Intentar parsear array JSON del LLM
        try:
            # Extraer el primer array JSON encontrado en la respuesta
            match = re.search(r"\[.*\]", response_text, re.DOTALL)
            if not match:
                raise ValueError("No se encontro array JSON en la respuesta")
            llm_items: list[dict] = json.loads(match.group())
        except Exception:
            logger.warning("llm_response_parse_failed", response=response_text[:200])
            # Fallback: construir desde source_nodes
            llm_items = [
                {"candidate_id": cid, "summary": "", "justification": ""}
                for cid in node_map
            ]

        results: list[CandidateResult] = []
        for item in llm_items:
            cid = item.get("candidate_id", "")
            score, payload = node_map.get(cid, (0.0, {}))
            results.append(
                CandidateResult(
                    candidate_id=cid,
                    file_name=payload.get("file_name", ""),
                    score=score,
                    summary=item.get("summary", ""),
                    justification=item.get("justification", ""),
                    contact_info=payload.get("contact_info", {}),
                    exp_years=int(payload.get("exp_years", 0)),
                    location=payload.get("location"),
                )
            )
        return results

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
        """Resumen simple sin LLM para modo fake."""
        words = chunk_text.split()
        preview = " ".join(words[:30])
        return f"[FAKE] {preview}..." if len(words) > 30 else f"[FAKE] {chunk_text}"

    @staticmethod
    def _fake_justification(job_description: str, chunk_text: str) -> str:
        """Justificacion placeholder sin LLM para modo fake."""
        keywords = job_description.split()[:5]
        return f"[FAKE] Candidato evaluado para: {chr(39).join(keywords)}. Fragmento: {chunk_text[:80]}"
