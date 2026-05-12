# Build stage – instala dependencias en una capa separada para aprovechar cache
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        pydantic \
        pydantic-settings \
        azure-identity \
        azure-keyvault-secrets \
        aiohttp \
        structlog \
        openai \
        qdrant-client \
        llama-index \
        llama-index-vector-stores-qdrant \
        "llama-index-embeddings-azure-openai" \
        "llama-index-llms-azure-openai"

# Runtime stage – imagen final ligera
FROM python:3.12-slim AS runtime

RUN useradd --create-home appuser
WORKDIR /app
USER appuser

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

COPY app/rag_service ./rag_service
COPY app/shared ./shared

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "rag_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
