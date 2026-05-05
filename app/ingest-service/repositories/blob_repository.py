"""Acceso a Azure Blob Storage – ingest-service."""
import structlog
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient

from shared.config import get_settings

logger = structlog.get_logger(__name__)


class BlobRepository:
    """Encapsula todas las operaciones sobre Azure Blob Storage."""

    def __init__(self) -> None:
        settings = get_settings()
        # DefaultAzureCredential usa Managed Identity en Azure y az login en local
        self._credential = DefaultAzureCredential()
        self._client = BlobServiceClient(
            account_url=settings.blob_account_url,
            credential=self._credential,
        )
        # Nombre del contenedor donde se almacenan los CVs
        self._container = settings.blob_container_name

    async def upload(self, blob_name: str, data: bytes, content_type: str = "application/pdf") -> str:
        """
        Sube datos a Blob Storage.
        Usa simple upload para archivos menores de 100 MB.
        Devuelve la URL del blob creado.
        """
        async with self._client.get_container_client(self._container) as container:
            blob_client = container.get_blob_client(blob_name)
            # overwrite=True permite resubir el mismo archivo si el proceso falla y se reintenta
            await blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings={"content_type": content_type},
            )
            logger.info("blob_uploaded", blob=blob_name, container=self._container)
            return blob_client.url

    async def download(self, blob_name: str) -> bytes:
        """Descarga un blob y devuelve su contenido en memoria."""
        async with self._client.get_container_client(self._container) as container:
            blob_client = container.get_blob_client(blob_name)
            stream = await blob_client.download_blob()
            return await stream.readall()
