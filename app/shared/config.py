"""
Shared – Configuracion global de la aplicacion.
Lee valores desde Azure Key Vault usando DefaultAzureCredential.
Nunca se usan os.environ directamente para secretos.
"""
from functools import lru_cache

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Lee variables desde el archivo .env; ignora claves desconocidas
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Modo fake (sin Azure) ─────────────────────────────────────────────────
    # Cuando es True, todos los adaptadores Azure quedan desactivados y se
    # usan implementaciones locales (blob en disco, embeddings deterministicos).
    use_fake_integrations: bool = False

    # ── Azure Key Vault ───────────────────────────────────────────────────────
    # Opcional: en fake mode no se conecta al vault
    key_vault_url: str = ""  # https://<vault-name>.vault.azure.net/

    # ── Azure Blob Storage ────────────────────────────────────────────────────
    blob_account_url: str = ""  # https://<account>.blob.core.windows.net
    blob_container_name: str = "cv-uploads"

    # Directorio local usado en fake mode para simular Blob Storage
    local_blob_path: str = "/tmp/talentrefine-blobs"

    # ── Qdrant ───────────────────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "talentrefine-candidates"

    # ── Azure OpenAI ─────────────────────────────────────────────────────────
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    azure_openai_chat_deployment: str = "gpt-4o-mini"

    # Dimensiones del vector de embedding (debe coincidir con el deployment real)
    fake_embedding_dimensions: int = 1536

    # ── App ──────────────────────────────────────────────────────────────────
    cors_allowed_origins: list[str] = ["http://localhost:8501"]
    app_env: str = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    # Instancia unica de la configuracion; el cache evita releerla en cada llamada
    return Settings()


@lru_cache
def get_secret_client() -> SecretClient:
    # Crea el cliente de Key Vault una sola vez usando DefaultAzureCredential
    settings = get_settings()
    credential = DefaultAzureCredential()
    return SecretClient(vault_url=settings.key_vault_url, credential=credential)


def get_secret(secret_name: str) -> str:
    """
    Devuelve el valor del secreto desde Key Vault.
    En fake mode devuelve cadena vacia sin conectar con Azure.
    """
    if get_settings().use_fake_integrations:
        return ""
    client = get_secret_client()
    return client.get_secret(secret_name).value or ""
