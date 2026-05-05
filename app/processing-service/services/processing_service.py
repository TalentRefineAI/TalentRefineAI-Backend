"""
Lógica de negocio de procesamiento de documentos.
Orquesta: descarga Blob → extracción PDF → chunking (LlamaIndex) → embedding → Qdrant.
"""
import uuid

import structlog
from llama_index.core import Document, Settings as LlamaSettings
from llama_index.core.node_parser import SentenceSplitter

from shared.exceptions import ProcessingError, VectorStoreError
from ..integrations.azure_openai import get_embed_model
from ..repositories.blob_repository import BlobRepository
from ..repositories.qdrant_repository import QdrantRepository

logger = structlog.get_logger(__name__)

# Tamano del fragmento de texto en tokens
CHUNK_SIZE = 512
# Solapamiento entre fragmentos consecutivos para preservar contexto
CHUNK_OVERLAP = 64


class ProcessingService:
    def __init__(self) -> None:
        self._blob = BlobRepository()
        self._qdrant = QdrantRepository()
        # Configura el modelo de embedding a nivel global de LlamaIndex
        LlamaSettings.embed_model = get_embed_model()
        # Divisor de oraciones con ventana deslizante para mantener contexto
        self._splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    async def process_document(self, doc_id: uuid.UUID, blob_name: str) -> None:
        """
        Pipeline completo: descarga → extrae → chunk → embed → persiste en Qdrant.
        Flujo asíncrono pensado para ejecutarse como background task.
        """
        logger.info("processing_start", doc_id=str(doc_id), blob=blob_name)

        # 1. Descarga el PDF desde Blob Storage
        try:
            pdf_bytes = await self._blob.download(blob_name)
        except Exception as exc:
            raise ProcessingError(f"No se pudo descargar el blob: {exc}") from exc

        # 2. Extracción de texto y metadata
        try:
            raw_text, metadata = self._extract(pdf_bytes, blob_name)
        except Exception as exc:
            raise ProcessingError(f"Error extrayendo texto del PDF: {exc}") from exc

        # 3. Chunking con LlamaIndex
        document = Document(text=raw_text, metadata=metadata)
        nodes = self._splitter.get_nodes_from_documents([document])

        # 4. Persistencia en Qdrant (el repositorio calcula los embeddings)
        try:
            await self._qdrant.upsert_nodes(doc_id, nodes, metadata)
        except Exception as exc:
            raise VectorStoreError(f"Error persistiendo en Qdrant: {exc}") from exc

        logger.info("processing_done", doc_id=str(doc_id), chunks=len(nodes))

    @staticmethod
    def _extract(pdf_bytes: bytes, file_name: str) -> tuple[str, dict]:
        """Extrae texto y metadata basica del PDF con pypdf."""
        import io
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        # Concatena el texto de todas las paginas; devuelve cadena vacia si la pagina no tiene texto
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        meta = reader.metadata or {}
        # Devuelve el texto completo y los metadatos del PDF para enriquecer el payload
        return text, {
            "file_name": file_name,
            "pages": len(reader.pages),
            "title": meta.get("/Title", ""),
            "author": meta.get("/Author", ""),
        }
