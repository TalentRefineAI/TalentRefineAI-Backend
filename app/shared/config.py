"""
Shared – Configuración global de la aplicación.
Lee valores desde Azure Key Vault usando DefaultAzureCredential.
Nunca se usan os.environ directamente para secretos.
"""
from functools import lru_cache

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Lee variables desde el archivo .env; ignora claves desconocidas
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # URL del vault donde se almacenan todos los secretos del proyecto
    key_vault_url: str  # https://<vault-name>.vault.azure.net/

    # Cuenta de almacenamiento donde se guardan los PDFs subidos
    blob_account_url: str  # https://<account>.blob.core.windows.net
    # Nombre del contenedor Blob que recibe los CVs
    blob_container_name: str = "cv-uploads"

    # Endpoint del cluster Qdrant donde se almacenan los vectores
    qdrant_url: str
    # Nombre de la coleccion unica que usa toda la aplicacion
    qdrant_collection: str = "talentrefine-candidates"

    # Endpoint del recurso Azure OpenAI
    azure_openai_endpoint: str
    # Version de la API de Azure OpenAI
    azure_openai_api_version: str = "2024-02-15-preview"
    # Nombre del deployment usado para generar embeddings
    azure_openai_embedding_deployment: str = "text-embedding-3-large"
    # Nombre del deployment de chat (GPT-4)
    azure_openai_chat_deployment: str = "gpt-4o"

    # Origenes permitidos por CORS; debe incluir la URL del frontend Streamlit
    cors_allowed_origins: list[str] = ["http://localhost:8501"]

    # Entorno de ejecucion: development | staging | production
    app_env: str = "development"
    # Activa adaptadores locales deterministas en lugar de Azure Blob/OpenAI/Key Vault
    use_fake_integrations: bool = False
    # Directorio compartido por ingest-service y processing-service en modo local_fake
    local_blob_path: str = "/tmp/talentrefine-blobs"
    # Dimension del vector usado en modo local_fake; compatible con text-embedding-3-large
    fake_embedding_dimensions: int = 1536
    # Nivel de log aplicado a structlog
    log_level: str = "INFO"

    @model_validator(mode="after")
    def prevent_fake_integrations_in_production(self) -> "Settings":
        """Evita arrancar adaptadores fake en entornos productivos."""
        production_envs = {"prod", "production", "pro"}
        if self.app_env.lower() in production_envs and self.use_fake_integrations:
            raise ValueError("USE_FAKE_INTEGRATIONS cannot be enabled in production")
        return self


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
    # Obtiene el valor de un secreto por nombre; devuelve cadena vacia si es None
    if get_settings().use_fake_integrations:
        return ""
    client = get_secret_client()
    return client.get_secret(secret_name).value or ""
