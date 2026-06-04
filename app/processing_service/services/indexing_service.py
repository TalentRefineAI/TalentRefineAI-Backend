"""Servicio de indexacion de documentos - processing-service."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

import fitz
import structlog
from llama_index.core import Settings as LlamaSettings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from pypdf import PdfReader

from shared.config import get_settings
from shared.exceptions import ProcessingError, VectorStoreError
from shared.fakes import fake_embedding
from shared.models import CandidatePayload
from ..integrations.azure_openai import get_embed_model, get_llm
from ..repositories.blob_repository import BlobRepository
from ..repositories.qdrant_repository import QdrantRepository

logger = structlog.get_logger(__name__)
CHUNK_SIZE = 1024
CHUNK_OVERLAP = 100

# Serializa el procesamiento dentro de la réplica: aunque lleguen varias peticiones
# concurrentes, los CVs se indexan de uno en uno para no saturar los 10K TPM del
# deployment de Azure OpenAI (embeddings + chat). Evita el throttling que tumbaba CVs.
_PROCESSING_SEMAPHORE = asyncio.Semaphore(1)

# Regex deterministas como refuerzo de la extraccion LLM
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")
# Telefono: secuencias de 9+ digitos admitiendo prefijo internacional, espacios,
# guiones y parentesis. Filtra ruido exigiendo al menos 9 digitos en total.
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}")


class IndexingService:
    def __init__(self) -> None:
        settings = get_settings()
        self._use_fake = settings.use_fake_integrations
        self._blob = BlobRepository()
        self._qdrant = QdrantRepository()
        self._splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        self._llm = None
        if not self._use_fake:
            LlamaSettings.embed_model = get_embed_model()
            self._llm = get_llm()

    async def process_document(self, doc_id: uuid.UUID, blob_name: str) -> None:
        log = logger.bind(doc_id=str(doc_id), blob_name=blob_name)
        # El semáforo serializa el trabajo pesado (descarga + LLM + embeddings + Qdrant)
        # para no saturar la cuota de Azure OpenAI cuando llegan varios CVs.
        async with _PROCESSING_SEMAPHORE:
            log.info("indexing_started")
            try:
                pdf_bytes = await self._blob.download(blob_name)
            except Exception as exc:
                raise ProcessingError(f"No se pudo descargar {blob_name}: {exc}") from exc
            try:
                raw_text, pdf_meta = self._extract(pdf_bytes, blob_name)
            except Exception as exc:
                raise ProcessingError(f"Error extrayendo texto de {blob_name}: {exc}") from exc
            clean = self._clean_text(raw_text)
            if not clean.strip():
                raise ProcessingError(f"PDF vacio: {blob_name}")
            # Extrae metadatos estructurados UNA sola vez; se denormalizan en cada chunk
            meta = await self._extract_metadata(clean)
            log.info(
                "metadata_extracted",
                has_email=bool(meta.get("email")),
                has_phone=bool(meta.get("phone")),
                exp_years=meta.get("exp_years", 0),
                n_skills=len(meta.get("skills", [])),
            )
            base_meta = CandidatePayload(
                candidate_id=str(doc_id), file_name=blob_name,
                contact_info={
                    "name": meta.get("name", ""),
                    "email": meta.get("email", ""),
                    "phone": meta.get("phone", ""),
                    "skills": meta.get("skills", []),
                },
                exp_years=int(meta.get("exp_years") or 0),
                location=meta.get("location"),
                chunk_text="",
            )
            await self.process_and_index_document(clean, base_meta)
            log.info("indexing_completed")

    async def process_and_index_document(self, text: str, metadata: CandidatePayload) -> None:
        chunks: list[str] = self._splitter.split_text(text)
        if not chunks:
            raise ProcessingError("SentenceSplitter no genero ningun chunk")
        logger.info("chunks_generated", n_chunks=len(chunks))
        if self._use_fake:
            embeddings = [fake_embedding(chunk) for chunk in chunks]
        else:
            try:
                # Timeout de 60s como red de seguridad: con el procesamiento serializado
                # no debería throttlear, pero evita cuelgues indefinidos por reintentos.
                embeddings = await asyncio.wait_for(
                    LlamaSettings.embed_model.aget_text_embedding_batch(chunks),
                    timeout=60.0,
                )
            except asyncio.TimeoutError as exc:
                raise ProcessingError("Timeout generando embeddings (>60s)") from exc
            except Exception as exc:
                raise ProcessingError(f"Error generando embeddings: {exc}") from exc
        # Include candidate_id and file_name in node metadata so LlamaIndex
        # can reconstruct them during RAG retrieval (grouping chunks by candidate).
        file_name = metadata.file_name.split("/")[-1] if "/" in metadata.file_name else metadata.file_name
        node_metadata = {"candidate_id": metadata.candidate_id, "file_name": file_name}
        nodes: list[TextNode] = [
            TextNode(text=chunk, embedding=emb, metadata=node_metadata)
            for chunk, emb in zip(chunks, embeddings)
        ]
        try:
            await self._qdrant.upsert_nodes(
                doc_id=uuid.UUID(metadata.candidate_id),
                nodes=nodes, doc_metadata=metadata.model_dump(),
            )
        except Exception as exc:
            logger.error("qdrant_upsert_error", exc_type=type(exc).__name__, exc_repr=repr(exc))
            raise VectorStoreError(f"Error subiendo nodos a Qdrant: {type(exc).__name__}: {exc}") from exc

    async def _extract_metadata(self, text: str) -> dict[str, Any]:
        """
        Extrae metadatos estructurados del CV una sola vez.
        Regex deterministas (email/telefono) + LLM para el resto de campos.
        Nunca lanza: ante cualquier fallo del LLM devuelve lo obtenido por regex.
        """
        # Refuerzo determinista: email y telefono por regex sobre el texto completo
        email_m = _EMAIL_RE.search(text)
        regex_email = email_m.group() if email_m else ""
        phone_m = _PHONE_RE.search(text)
        regex_phone = phone_m.group().strip() if phone_m else ""

        fallback = {
            "name": "",
            "email": regex_email,
            "phone": regex_phone,
            "location": None,
            "exp_years": 0,
            "skills": [],
        }

        # En modo fake (o sin LLM configurado) no se llama a Azure
        if self._use_fake or self._llm is None:
            return fallback

        # Truncar a 3000 caracteres: suficiente para extraer contacto y skills sin agotar
        # los 10K TPM del deployment. El email/teléfono casi siempre están en el primer
        # tercio del CV; los años de experiencia se deducen de las secciones iniciales.
        text_for_llm = text[:3000]

        prompt = (
            "Eres un experto en seleccion de personal IT. Extrae los metadatos del "
            "siguiente CV y devuelve SOLO un objeto JSON valido (sin markdown ni texto "
            "adicional) con EXACTAMENTE estos campos:\n"
            '- "name": nombre completo del candidato (string, "" si no aparece)\n'
            '- "email": correo electronico (string, "" si no aparece)\n'
            '- "phone": telefono (string, "" si no aparece)\n'
            '- "location": ciudad o pais de residencia (string o null)\n'
            '- "exp_years": anos de experiencia profesional estimados por las fechas (entero)\n'
            '- "skills": lista de habilidades tecnicas principales, maximo 10 (array de strings)\n\n'
            "CV (extracto):\n" + text_for_llm
        )

        try:
            # Timeout de 25 s: evita bloqueos por retry automático del cliente LlamaIndex
            # ante throttling (429) del deployment con 10K TPM.
            response = await asyncio.wait_for(self._llm.acomplete(prompt), timeout=25.0)
            match = re.search(r"\{.*\}", str(response), re.DOTALL)
            if not match:
                raise ValueError("No JSON object found in LLM response")
            data = json.loads(match.group())
        except asyncio.TimeoutError:
            logger.warning("metadata_llm_timeout", text_len=len(text_for_llm))
            return fallback
        except Exception as exc:
            logger.warning("metadata_llm_extraction_failed", error=str(exc))
            return fallback

        # El LLM tiene prioridad; el regex rellena email/phone si el LLM los dejo vacios
        return {
            "name": (data.get("name") or "").strip(),
            "email": (data.get("email") or regex_email or "").strip(),
            "phone": (data.get("phone") or regex_phone or "").strip(),
            "location": data.get("location") or None,
            "exp_years": int(data.get("exp_years") or 0),
            "skills": data.get("skills") if isinstance(data.get("skills"), list) else [],
        }

    @staticmethod
    def _extract(pdf_bytes: bytes, file_name: str) -> tuple[str, dict[str, Any]]:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = [page.get_text() for page in doc]
        full_text = chr(10).join(pages)
        doc.close()
        import io
        reader = PdfReader(io.BytesIO(pdf_bytes))
        meta_raw = reader.metadata or {}
        meta = {k.lstrip("/"): v for k, v in meta_raw.items() if isinstance(v, str)}
        meta["file_name"] = file_name
        return full_text, meta

    @staticmethod
    def _clean_text(text: str) -> str:
        text = "".join(ch for ch in text if ch in chr(9)+chr(10)+chr(13) or (32 <= ord(ch) != 127))
        while chr(10)*3 in text:
            text = text.replace(chr(10)*3, chr(10)*2)
        text = re.sub(r" {2,}", " ", text)
        return text.strip()
