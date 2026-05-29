"""Acceso a Azure Blob Storage – processing-service (descarga)."""
import structlog
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient

from shared.config import get_settings

logger = structlog.get_logger(__name__)


class BlobRepository:
    """Encapsula las operaciones de descarga desde Azure Blob Storage."""

    def __init__(self) -> None:
        settings = get_settings()
        self._use_fake = settings.use_fake_integrations
        self._container = settings.blob_container_name
        if not self._use_fake:
            self._credential = DefaultAzureCredential()
            self._client = BlobServiceClient(
                account_url=settings.blob_account_url,
                credential=self._credential,
            )

    async def download(self, blob_name: str) -> bytes:
        """Descarga el PDF desde Blob y lo carga en memoria para procesarlo."""
        if self._use_fake:
            from shared.fakes import local_blob_download
            data = local_blob_download(blob_name)
            logger.info("fake_blob_downloaded", blob=blob_name)
            return data

        async with self._client.get_container_client(self._container) as container:
            blob_client = container.get_blob_client(blob_name)
            stream = await blob_client.download_blob()
            return await stream.readall()
