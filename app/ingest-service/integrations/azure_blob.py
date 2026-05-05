"""Cliente Azure Blob Storage – integración compartida del ingest-service."""
from functools import lru_cache

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient

from shared.config import get_settings


@lru_cache
def get_blob_service_client() -> BlobServiceClient:
    """Singleton del cliente Blob usando DefaultAzureCredential (Managed Identity)."""
    settings = get_settings()
    credential = DefaultAzureCredential()
    return BlobServiceClient(account_url=settings.blob_account_url, credential=credential)
