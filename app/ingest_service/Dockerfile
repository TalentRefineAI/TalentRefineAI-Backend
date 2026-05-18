# Build stage – instala dependencias en una capa separada para aprovechar cache
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements/ingest.txt requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Runtime stage – imagen final ligera
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

# Usuario sin privilegios para ejecutar la app
RUN useradd --create-home appuser
WORKDIR /app
USER appuser

# Copia solo los paquetes instalados desde el builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copia el código fuente
COPY app/ingest_service ./ingest_service
COPY app/shared ./shared

EXPOSE 8000

CMD ["uvicorn", "ingest_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
