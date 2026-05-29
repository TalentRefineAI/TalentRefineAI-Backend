"""Servicio de indexacion de documentos - processing-service."""
from __future__ import annotations

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
from ..integrations.azure_openai import get_embed_model
from ..repositories.blob_repository import BlobRepository
from ..repositories.qdrant_repository import QdrantRepository

logger = structlog.get_logger(__name__)
CHUNK_SIZE = 1024
CHUNK_OVERLAP = 100


class IndexingService:
    def __init__(self) -> None:
        settings = get_settings()
        self._use_fake = settings.use_fake_integrations
        self._blob = BlobRepository()
        self._qdrant = QdrantRepository()
        self._splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        if not self._use_fake:
            LlamaSettings.embed_model = get_embed_model()

    async def process_document(self, doc_id: uuid.UUID, blob_name: str) -> None:
        log = logger.bind(doc_id=str(doc_id), blob_name=blob_name)
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
        base_meta = CandidatePayload(
            candidate_id=str(doc_id), file_name=blob_name,
            contact_info=pdf_meta, exp_years=0, location=None, chunk_text="",
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
                embeddings = await LlamaSettings.embed_model.aget_text_embedding_batch(chunks)
            except Exception as exc:
                raise ProcessingError(f"Error generando embeddings: {exc}") from exc
        nodes: list[TextNode] = [TextNode(text=chunk, embedding=emb) for chunk, emb in zip(chunks, embeddings)]
        try:
            await self._qdrant.upsert_nodes(
                doc_id=uuid.UUID(metadata.candidate_id),
                nodes=nodes, doc_metadata=metadata.model_dump(),
            )
        except Exception as exc:
            logger.error("qdrant_upsert_error", exc_type=type(exc).__name__, exc_repr=repr(exc))
            raise VectorStoreError(f"Error subiendo nodos a Qdrant: {type(exc).__name__}: {exc}") from exc

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
