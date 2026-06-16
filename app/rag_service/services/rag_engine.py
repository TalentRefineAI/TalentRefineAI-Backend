"""Motor RAG con busqueda directa via Qdrant + extraccion estructurada via LLM."""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import structlog
from llama_index.core import Settings as LlamaSettings
from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

from shared.config import get_settings
from shared.exceptions import LLMError, VectorStoreError
from shared.fakes import fake_embedding
from ..integrations.azure_openai import get_embed_model, get_llm
from ..repositories.qdrant_repository import QdrantRepository
from ..routers.schemas import CandidateResult, SearchRequest, SearchResponse

logger = structlog.get_logger(__name__)


def _log_success(log, event: str, **fields) -> None:
    """Éxito final de negocio que debe sobrevivir a LOG_LEVEL=SUCCESS/WARNING en Azure.

    PLACEHOLDER (dependencia Integrante 2): mientras shared/logging.py no registre el nivel
    custom SUCCESS=25 ni exponga logger.success(), se emite a INFO. Cuando Integrante 2
    aterrice el nivel, este helper usará logger.success() automáticamente. Se puede entonces
    sustituir por la llamada directa log.success(event, **fields) y borrar este helper.
    """
    success = getattr(log, "success", None)
    (success if callable(success) else log.info)(event, **fields)


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
        start = time.perf_counter()
        try:
            if self._use_fake:
                resp = await self._search_fake(request)
            else:
                resp = await self._search_azure(request)
        except Exception as exc:
            logger.error("rag_search_failed", error=str(exc), error_type=type(exc).__name__)
            raise
        # No se loggea job_description; solo metadatos de la consulta y del resultado.
        _log_success(
            logger,
            "rag_search_completed",
            top_k=request.top_k,
            min_exp_years=request.min_exp_years,
            has_location_filter=request.required_location is not None,
            results_count=len(resp.results),
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return resp

    # ------------------------------------------------------------------ #
    # Implementacion Azure (Qdrant directo + OpenAI)                     #
    # ------------------------------------------------------------------ #

    async def _search_azure(self, request: SearchRequest) -> SearchResponse:
        client = self._qdrant_repo._client
        collection = self._qdrant_repo._collection

        # Guard: coleccion no existente o vacia — con reintentos por si Qdrant
        # acaba de arrancar y la coleccion aun se esta cargando desde disco.
        points_count = 0
        _max_attempts = 4
        _collection_ready = False
        for _attempt in range(_max_attempts):
            try:
                if await client.collection_exists(collection):
                    info = await client.get_collection(collection)
                    points_count = info.points_count or 0
                    if points_count > 0:
                        _collection_ready = True
                        logger.info(
                            "qdrant_collection_ready",
                            points_count=points_count,
                            attempt=_attempt + 1,
                        )
                        break
                    logger.warning(
                        "qdrant_collection_empty_or_loading",
                        attempt=_attempt + 1,
                        points_count=points_count,
                    )
                else:
                    logger.warning("qdrant_collection_missing", attempt=_attempt + 1)
            except Exception as exc:
                logger.warning(
                    "qdrant_collection_check_failed",
                    error=str(exc),
                    attempt=_attempt + 1,
                )
            if _attempt < _max_attempts - 1:
                await asyncio.sleep(2)

        if not _collection_ready:
            logger.warning(
                "qdrant_collection_not_ready",
                max_attempts=_max_attempts,
                points_count=points_count,
            )
            # Flujo no fallido pero degradado: se devuelve respuesta vacía sin 500.
            logger.warning("rag_search_degraded", reason="qdrant_not_ready", points_count=points_count)
            return SearchResponse(results=[])

        # Generar embedding de la consulta directamente via Azure OpenAI
        try:
            query_embedding = await LlamaSettings.embed_model.aget_text_embedding(request.job_description)
        except Exception as exc:
            raise LLMError(f"Error generando embedding: {exc}") from exc

        # Filtros nativos de Qdrant
        qdrant_filter = self._build_qdrant_filter(request)

        logger.info(
            "qdrant_search_attempt",
            collection=collection,
            points_in_collection=points_count,
            embedding_dim=len(query_embedding),
            limit=request.top_k * 3,
            has_filter=qdrant_filter is not None,
        )

        # Busqueda vectorial directa con query_points (qdrant_client >= 1.9)
        try:
            qr = await client.query_points(
                collection_name=collection,
                query=list(query_embedding),
                limit=request.top_k * 3,
                with_payload=True,
                query_filter=qdrant_filter,
            )
            raw_points = qr.points
        except Exception as exc:
            logger.error("qdrant_search_failed", error=str(exc), error_type=type(exc).__name__)
            raise VectorStoreError(f"Error en busqueda vectorial: {exc}") from exc

        logger.info("qdrant_search_result", n_points=len(raw_points), points_in_collection=points_count)

        if not raw_points:
            return SearchResponse(results=[])

        # Agrupar por candidate_id: mejor score coseno y acumular chunks
        candidate_scores: dict[str, float] = {}
        candidate_file: dict[str, str] = {}
        candidate_chunks: dict[str, list[str]] = {}
        candidate_payloads: dict[str, dict] = {}

        for point in raw_points:
            payload = point.payload or {}
            cid = payload.get("candidate_id") or str(point.id)
            score = float(point.score) if point.score is not None else 0.0
            if cid not in candidate_scores or score > candidate_scores[cid]:
                candidate_scores[cid] = score
                candidate_file[cid] = payload.get("file_name", "")
            candidate_chunks.setdefault(cid, []).append(payload.get("chunk_text", ""))
            candidate_payloads[cid] = {k: v for k, v in payload.items() if k != "chunk_text"}

        # Top-K candidatos por score coseno
        top_cids = sorted(candidate_scores, key=candidate_scores.__getitem__, reverse=True)[: request.top_k]

        # LLM extrae informacion estructurada de todos los candidatos en una sola llamada
        # Usar claves enteras simples para el LLM: evita que el LLM modifique IDs con
        # caracteres especiales (espacios, barras) que rompen el matching posterior
        _cid_list = list(top_cids)
        _idx_to_cid = {str(i): cid for i, cid in enumerate(_cid_list)}
        try:
            _llm_data_raw = await self._extract_all_candidates(
                job_description=request.job_description,
                candidate_chunks={str(i): candidate_chunks[cid] for i, cid in enumerate(_cid_list)},
            )
            llm_data = {_idx_to_cid[k]: v for k, v in _llm_data_raw.items() if k in _idx_to_cid}
            logger.info("llm_data_mapped", candidates_queried=len(_cid_list), candidates_with_llm_data=len(llm_data))
        except Exception as exc:
            logger.warning("llm_extraction_failed", error=str(exc))
            # Degradado: sin datos del LLM, el ranking cae a solo-coseno (fallback).
            logger.warning("rag_search_degraded", reason="llm_extraction_fallback")
            llm_data = {}

        # Score hibrido: 40% coseno normalizado + 60% relevance_score del LLM
        _cosine_vals = [candidate_scores[cid] for cid in top_cids]
        _min_c = min(_cosine_vals) if _cosine_vals else 0.0
        _max_c = max(_cosine_vals) if _cosine_vals else 1.0
        _spread = (_max_c - _min_c) if _max_c > _min_c else 1.0
        final_scores: dict[str, float] = {}
        for cid in top_cids:
            norm_cosine = (candidate_scores[cid] - _min_c) / _spread
            if llm_data:
                llm_rel = float(llm_data.get(cid, {}).get("relevance_score") or 0) / 100.0
                final_scores[cid] = round(0.4 * norm_cosine + 0.6 * llm_rel, 4)
            else:
                final_scores[cid] = round(candidate_scores[cid], 4)
        top_cids = sorted(top_cids, key=final_scores.__getitem__, reverse=True)

        # Filtro post-LLM por exp_years — solo filtrar candidatos que tenemos data LLM
        # Los que no tienen data LLM, usaremos payload de Qdrant como fallback
        if request.min_exp_years is not None and request.min_exp_years > 0:
            filtered_cids = []
            for cid in top_cids:
                # Prioridad al payload (extraido en procesamiento, fiable); fallback al LLM
                payload_exp = int(candidate_payloads.get(cid, {}).get("exp_years") or 0)
                llm_exp = int(llm_data.get(cid, {}).get("exp_years") or 0)
                exp_years_val = payload_exp or llm_exp

                if exp_years_val >= int(request.min_exp_years):
                    filtered_cids.append(cid)
            top_cids = filtered_cids
            logger.info(
                "exp_years_post_filter",
                min_exp_years=request.min_exp_years,
                candidates_checked=len(top_cids),
                llm_data_count=len(llm_data),
            )

        # Filtro post-LLM por location (substring, case-insensitive)
        # El campo location es free-text LLM; MatchValue exacto nunca hace match.
        # Buscamos substring: "Madrid" debe matchear "Madrid, Spain", "Madrid (España)", etc.
        if request.required_location:
            loc_query = request.required_location.lower()
            filtered_cids = []
            for cid in top_cids:
                payload_loc = (candidate_payloads.get(cid, {}).get("location") or "").lower()
                llm_loc = (llm_data.get(cid, {}).get("location") or "").lower()
                candidate_loc = payload_loc or llm_loc
                if loc_query in candidate_loc:
                    filtered_cids.append(cid)
            top_cids = filtered_cids
            logger.info(
                "location_post_filter",
                required_location=request.required_location,
                candidates_after_filter=len(top_cids),
            )

        results: list[CandidateResult] = []
        for cid in top_cids:
            info = llm_data.get(cid, {})
            payload = candidate_payloads.get(cid, {})
            # Metadatos: prioridad al payload (extraido en el procesamiento, fiable e
            # independiente de que chunk se haya recuperado); fallback al LLM para datos
            # antiguos sin metadatos denormalizados. summary/justification dependen de la
            # query → siempre del LLM.
            p_contact = payload.get("contact_info") or {}
            results.append(
                CandidateResult(
                    candidate_id=cid,
                    file_name=candidate_file[cid],
                    score=final_scores[cid],
                    summary=info.get("summary", ""),
                    justification=info.get("justification", ""),
                    contact_info={
                        "name": p_contact.get("name") or info.get("name", ""),
                        "email": p_contact.get("email") or info.get("email", ""),
                        "phone": p_contact.get("phone") or info.get("phone", ""),
                        "skills": p_contact.get("skills") or info.get("skills", []),
                    },
                    exp_years=int(payload.get("exp_years") or info.get("exp_years") or 0),
                    location=payload.get("location") or info.get("location"),
                )
            )

        return SearchResponse(results=results)

    # ------------------------------------------------------------------ #
    # Extraccion estructurada via LLM                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _extract_all_candidates(
        candidate_chunks: dict[str, list[str]],
        job_description: str,
    ) -> dict[str, dict[str, Any]]:
        """
        Llama al LLM una sola vez para extraer informacion estructurada de todos
        los candidatos. candidate_chunks: {cid: [chunk_text, ...]}
        Devuelve dict keyed por candidate_id.
        """
        sections: list[str] = []
        for cid, chunks in candidate_chunks.items():
            text = "\n\n".join(chunks)
            sections.append(f"[CANDIDATE_ID: {cid}]\n{text}")

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
            '- "justification": motivo de encaje con el puesto en 2-3 frases (string)\n'
            '- "relevance_score": puntuacion entera de 0 a 100 que indica el encaje del candidato '
            'con el puesto. Rubrica: 90-100 excelente (cubre todos los requisitos clave); '
            '70-89 buen encaje (mayoria de requisitos); 50-69 encaje parcial; '
            '30-49 encaje debil; 0-29 sin encaje relevante.\n\n'
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
    def _build_qdrant_filter(request: SearchRequest) -> Filter | None:
        """Construye filtros nativos de Qdrant para busqueda directa.

        NOTA: exp_years NO se filtra a nivel Qdrant porque el servicio de procesamiento
        almacena exp_years=0 para todos los CVs (valor poco fiable). El filtro por
        exp_years se aplica despues de la extraccion LLM, que si lee el texto real del CV.

        NOTA: location NO se filtra a nivel Qdrant porque es free-text LLM ("Madrid, Spain")
        que nunca hace match exacto con MatchValue ("Madrid"). El filtro por location se
        aplica Python-side despues de retrieval, con substring/contains (case-insensitive).
        """
        conditions = []
        # location filtering moved to Python-side post-filter (_search_azure, after exp_years)
        return Filter(must=conditions) if conditions else None

    @staticmethod
    def _payload_matches_filters(payload: dict, request: SearchRequest) -> bool:
        """Filtra Python-side en modo fake (Qdrant local no aplica los filtros)."""
        if request.min_exp_years is not None:
            if int(payload.get("exp_years", 0)) < request.min_exp_years:
                return False
        if request.required_location:
            loc = payload.get("location", "") or ""
            if request.required_location.lower() not in loc.lower():
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
        """Devuelve todos los candidatos unicos almacenados en Qdrant."""
        return await self._qdrant_repo.list_candidates()

    async def delete_all_candidates(self) -> None:
        """Elimina todos los candidatos del vector store."""
        await self._qdrant_repo.delete_all()
