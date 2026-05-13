"""
processing-service - Punto de entrada FastAPI.
Extrae texto/metadata de PDFs, hace chunking y persiste en Qdrant.
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from shared.config import get_settings
from .routers import processing

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("processing-service starting")
    yield
    logger.info("processing-service shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="TalentRefine - Processing Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Este servicio es interno; no expone CORS al exterior

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        # Comprueba que el servicio esta vivo; no requiere conexion con Azure
        return {"status": "ok", "service": "processing-service"}

    app.include_router(processing.router, prefix="/processing", tags=["processing"])

    return app


# Instancia lista para ser servida por uvicorn
app = create_app()
