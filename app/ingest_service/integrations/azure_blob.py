"""Cliente Azure Blob Storage – integración compartida del ingest-service."""
from functools import lru_cache

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from shared.config import get_settings


@lru_cache
def get_blob_service_client() -> BlobServiceClient:
    # Crea una unica instancia del cliente para reutilizar la conexion
    # DefaultAzureCredential resuelve la autenticacion segun el entorno de ejecucion
    settings = get_settings()
    credential = DefaultAzureCredential()
    return BlobServiceClient(account_url=settings.blob_account_url, credential=credential)
