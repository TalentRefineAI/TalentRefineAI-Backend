"""Procesamiento local fake para pruebas de integracion sin Azure OpenAI."""

import uuid

import structlog
from shared.fakes import fake_embedding

from .processing_service import ProcessingService

logger = structlog.get_logger(__name__)


class FakeProcessingService(ProcessingService):
    """Variante de processing que genera embeddings deterministas locales."""

    def __init__(self) -> None:
        self._init_common_dependencies()
        self._embed_model = None

    def _get_embeddings(self, chunks: list[str], doc_id: uuid.UUID) -> list[list[float]]:
        embeddings = [fake_embedding(chunk) for chunk in chunks]
        logger.info(
            "fake_embeddings_generated",
            doc_id=str(doc_id),
            chunks=len(chunks),
            dimensions=len(embeddings[0]) if embeddings else 0,
        )
        return embeddings
