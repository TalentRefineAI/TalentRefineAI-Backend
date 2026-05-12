"""
Logica de negocio de procesamiento de documentos.
Orquesta: descarga Blob → extraccion PDF [BK-04] → limpieza regex [BK-04]
         → chunking RecursiveCharacterTextSplitter [BK-05] → embedding → Qdrant.
"""
import io
import re
import uuid

import fitz  # PyMuPDF: extractor principal por mayor compatibilidad con fuentes embebidas
import structlog
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_index.core.schema import TextNode
from pypdf import PdfReader
from shared.config import get_settings
from shared.exceptions import ProcessingError, VectorStoreError

from ..integrations.azure_openai import get_embed_model
from ..repositories.blob_repository import BlobRepository
from ..repositories.qdrant_repository import QdrantRepository

logger = structlog.get_logger(__name__)

# [BK-05] Tamano del fragmento en caracteres (~1000 tokens)
CHUNK_SIZE = 1000
# [BK-05] Solapamiento entre fragmentos consecutivos para preservar contexto
CHUNK_OVERLAP = 200

# [BK-05] Separadores usados por RecursiveCharacterTextSplitter en orden de prioridad
_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


class ProcessingService:
    def __init__(self) -> None:
        settings = get_settings()
        self._blob = BlobRepository()
        self._qdrant = QdrantRepository()
        self._use_fake_integrations = settings.use_fake_integrations
        self._embed_model = None if self._use_fake_integrations else get_embed_model()
        # [BK-05] Divisor recursivo que intenta preservar parrafos y oraciones
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=_SEPARATORS,
        )

    async def process_document(self, doc_id: uuid.UUID, blob_name: str) -> None:
        """
        Pipeline completo: descarga → extrae → limpia → chunk → embed → Qdrant.
        Flujo asincrono pensado para ejecutarse como background task.
        """
        logger.info("processing_started", doc_id=str(doc_id), blob_name=blob_name)

        # 1. Descarga el PDF desde Blob Storage [BK-03]
        try:
            pdf_bytes = await self._blob.download(blob_name)
        except Exception as exc:
            raise ProcessingError(f"No se pudo descargar el blob: {exc}") from exc

        # 2. Extraccion de texto y metadata del PDF [BK-04]
        try:
            raw_text, metadata = self._extract(pdf_bytes, blob_name)
        except Exception as exc:
            raise ProcessingError(f"Error extrayendo texto del PDF: {exc}") from exc

        # 3. Limpieza del texto con expresiones regulares [BK-04]
        clean_text = self._clean_text(raw_text)
        logger.info(
            "pdf_text_extracted",
            doc_id=str(doc_id),
            blob_name=blob_name,
            pages=metadata.get("pages"),
            characters=len(clean_text),
        )

        # 4. Chunking con RecursiveCharacterTextSplitter [BK-05]
        chunks = self._splitter.split_text(clean_text)
        logger.info("chunks_created", doc_id=str(doc_id), blob_name=blob_name, chunks=len(chunks))

        # 5. Embedding en bloque: una llamada a la API por lote de chunks
        embeddings = (
            [None for _ in chunks]
            if self._use_fake_integrations
            else self._embed_model.get_text_embedding_batch(chunks)
        )

        # 6. Construye TextNode con el texto y su embedding para persistir en Qdrant
        nodes = [
            TextNode(text=chunk, embedding=emb)
            for chunk, emb in zip(chunks, embeddings)
        ]

        # 7. Persistencia en Qdrant
        try:
            await self._qdrant.upsert_nodes(doc_id, nodes, metadata)
        except Exception as exc:
            raise VectorStoreError(f"Error persistiendo en Qdrant: {exc}") from exc

        logger.info("processing_done", doc_id=str(doc_id), blob_name=blob_name, chunks=len(nodes))

    @staticmethod
    def _extract(pdf_bytes: bytes, file_name: str) -> tuple[str, dict]:
        """
        [BK-04] Extrae texto y metadata del PDF.
        Usa PyMuPDF (fitz) como extractor principal por su compatibilidad con fuentes embebidas.
        Obtiene la metadata de cabecera con pypdf.
        """
        # Extraccion de texto con PyMuPDF: soporta Europass, PDFs con fuentes no estandar
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        num_pages = doc.page_count
        doc.close()

        # Metadata desde pypdf (cabecera /Title, /Author)
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            meta = reader.metadata or {}
        except Exception:
            meta = {}

        return text, {
            "file_name": file_name,
            "pages": num_pages,
            "title": meta.get("/Title", ""),
            "author": meta.get("/Author", ""),
        }

    @staticmethod
    def _clean_text(text: str) -> str:
        """
        [BK-04] Limpia el texto extraido del PDF aplicando expresiones regulares.
        Elimina ruido tipico de PDFs: saltos multiples, caracteres raros y espacios extra.
        """
        # Elimina caracteres de control y no imprimibles (excepto saltos de linea)
        text = re.sub(r'[^\x20-\x7EáéíóúÁÉÍÓÚüÜñÑ\n]', ' ', text)
        # Colapsa tres o mas saltos de linea consecutivos en dos (separa parrafos)
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Colapsa espacios multiples en uno solo
        text = re.sub(r'[ \t]+', ' ', text)
        # Elimina espacios al inicio y final de cada linea
        text = '\n'.join(line.strip() for line in text.splitlines())
        return text.strip()
