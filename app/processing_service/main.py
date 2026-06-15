"""
processing-service - Punto de entrada FastAPI.
Extrae texto/metadata de PDFs, hace chunking y persiste en Qdrant.
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from shared.logging import configure_logging
from shared.request_logging import RequestLoggingMiddleware

from .routers import processing

configure_logging("processing-service")
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("processing-service starting")
    yield
    logger.info("processing-service shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="TalentRefine - Processing Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(RequestLoggingMiddleware)

    # Este servicio es interno; no expone CORS al exterior

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "processing-service"}

    app.include_router(processing.router, prefix="/processing", tags=["processing"])

    return app


# Instancia lista para ser servida por uvicorn
app = create_app()
