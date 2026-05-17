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
        azure-storage-blob \
        aiohttp \
        azure-keyvault-secrets \
        structlog \
        pymupdf \
        pypdf \
        langchain-text-splitters \
        openai \
        qdrant-client \
        llama-index \
        "llama-index-embeddings-azure-openai"

# Runtime stage – imagen final ligera
FROM python:3.12-slim AS runtime

RUN useradd --create-home appuser
WORKDIR /app
USER appuser

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

COPY app/processing_service ./processing_service
COPY app/shared ./shared

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "processing_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
