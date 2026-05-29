"""Acceso a Azure Blob Storage – ingest-service."""
import structlog
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

from shared.config import get_settings
from shared.fakes import local_blob_download, local_blob_upload

logger = structlog.get_logger(__name__)


class BlobRepository:
    """Encapsula todas las operaciones sobre Azure Blob Storage."""

    def __init__(self) -> None:
        settings = get_settings()
        self._account_url = settings.blob_account_url
        self._container = settings.blob_container_name
        self._use_fake = settings.use_fake_integrations
        # Cliente y credencial se crean de forma lazy para no conectar con Azure al importar
        self._credential: DefaultAzureCredential | None = None
        self._client: BlobServiceClient | None = None

    def _get_client(self) -> BlobServiceClient:
        # Crea el cliente Blob solo cuando se necesita por primera vez
        if self._client is None:
            self._credential = DefaultAzureCredential()
            self._client = BlobServiceClient(
                account_url=self._account_url,
                credential=self._credential,
            )
        return self._client

    async def upload(self, blob_name: str, data: bytes, content_type: str = "application/pdf") -> str:
        """
        Sube datos a Blob Storage.
        En fake mode guarda en disco local y devuelve una URL file://.
        """
        if self._use_fake:
            url = local_blob_upload(blob_name, data)
            logger.info("fake_blob_uploaded", blob=blob_name, url=url)
            return url

        async with self._get_client().get_container_client(self._container) as container:
            blob_client = container.get_blob_client(blob_name)
            await blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
            logger.info("blob_uploaded", blob=blob_name, container=self._container)
            return blob_client.url

    async def download(self, blob_name: str) -> bytes:
        """Descarga un blob y devuelve su contenido en memoria."""
        if self._use_fake:
            data = local_blob_download(blob_name)
            logger.info("fake_blob_downloaded", blob=blob_name)
            return data

        async with self._get_client().get_container_client(self._container) as container:
            blob_client = container.get_blob_client(blob_name)
            stream = await blob_client.download_blob()
            return await stream.readall()
