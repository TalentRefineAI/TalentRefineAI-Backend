"""
Shared – Configuración global de la aplicación.
Lee valores desde Azure Key Vault usando DefaultAzureCredential.
Nunca se usan os.environ directamente para secretos.
"""
from functools import lru_cache

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Azure Key Vault ───────────────────────────────────────────────────────
    key_vault_url: str  # https://<vault-name>.vault.azure.net/

    # ── Azure Blob Storage ────────────────────────────────────────────────────
    blob_account_url: str  # https://<account>.blob.core.windows.net
    blob_container_name: str = "cv-uploads"

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_url: str
    qdrant_collection: str = "talentrefine-candidates"

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    azure_openai_endpoint: str
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_embedding_deployment: str = "text-embedding-3-large"
    azure_openai_chat_deployment: str = "gpt-4o"

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_allowed_origins: list[str] = ["http://localhost:8501"]

    # ── Internos ──────────────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_secret_client() -> SecretClient:
    """Cliente de Key Vault usando Managed Identity / DefaultAzureCredential."""
    settings = get_settings()
    credential = DefaultAzureCredential()
    return SecretClient(vault_url=settings.key_vault_url, credential=credential)


def get_secret(secret_name: str) -> str:
    """Recupera un secreto desde Azure Key Vault."""
    client = get_secret_client()
    return client.get_secret(secret_name).value or ""
