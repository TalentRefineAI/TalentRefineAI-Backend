"""Clientes Azure OpenAI (embedding + chat) – rag-service."""
from functools import lru_cache

from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.llms.azure_openai import AzureOpenAI
from shared.config import get_secret, get_settings


@lru_cache
def get_embed_model() -> AzureOpenAIEmbedding:
    # Singleton del modelo de embedding; reutiliza la misma instancia en todas las llamadas
    settings = get_settings()
    # La misma clave sirve para embedding y chat en el mismo recurso Azure OpenAI
    api_key = get_secret("azure-openai-api-key")
    return AzureOpenAIEmbedding(
        model=settings.azure_openai_embedding_model,
        deployment_name=settings.azure_openai_embedding_deployment,
        api_key=api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )


@lru_cache
def get_llm() -> AzureOpenAI:
    # Singleton del modelo de chat GPT-4; evita crear multiples conexiones
    settings = get_settings()
    api_key = get_secret("azure-openai-api-key")
    return AzureOpenAI(
        model=settings.azure_openai_chat_model,
        deployment_name=settings.azure_openai_chat_deployment,
        api_key=api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )
