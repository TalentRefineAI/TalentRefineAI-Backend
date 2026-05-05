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
    # Nivel de log aplicado a structlog
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
    # Obtiene el valor de un secreto por nombre; devuelve cadena vacia si es None
    client = get_secret_client()
    return client.get_secret(secret_name).value or ""
