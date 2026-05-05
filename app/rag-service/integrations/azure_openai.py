"""Clientes Azure OpenAI (embedding + chat) – rag-service."""
from functools import lru_cache

from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.llms.azure_openai import AzureOpenAI

from shared.config import get_secret, get_settings


@lru_cache
def get_embed_model() -> AzureOpenAIEmbedding:
    settings = get_settings()
    api_key = get_secret("azure-openai-api-key")
    return AzureOpenAIEmbedding(
        model=settings.azure_openai_embedding_deployment,
        deployment_name=settings.azure_openai_embedding_deployment,
        api_key=api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )


@lru_cache
def get_llm() -> AzureOpenAI:
    settings = get_settings()
    api_key = get_secret("azure-openai-api-key")
    return AzureOpenAI(
        model=settings.azure_openai_chat_deployment,
        deployment_name=settings.azure_openai_chat_deployment,
        api_key=api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )
