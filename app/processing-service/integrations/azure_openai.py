"""Cliente Azure OpenAI para embeddings – processing-service."""
from functools import lru_cache

from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding

from shared.config import get_secret, get_settings


@lru_cache
def get_embed_model() -> AzureOpenAIEmbedding:
    """
    Devuelve el modelo de embedding de Azure OpenAI.
    La API key se recupera desde Key Vault; nunca se hardcodea.
    """
    settings = get_settings()
    api_key = get_secret("azure-openai-api-key")

    return AzureOpenAIEmbedding(
        model=settings.azure_openai_embedding_deployment,
        deployment_name=settings.azure_openai_embedding_deployment,
        api_key=api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )
