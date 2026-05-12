"""
ingest-service – Punto de entrada FastAPI.
Recibe PDFs, valida, sube a Azure Blob y encola el procesamiento.
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.config import get_settings
from shared.logging import configure_logging

from .routers import upload

configure_logging("ingest-service")
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Acciones al arrancar y apagar el servicio
    logger.info("ingest-service starting")
    yield
    logger.info("ingest-service shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="TalentRefine – Ingest Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Solo permite peticiones desde el origen del frontend configurado
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Registra el router de upload bajo el prefijo /upload (BK-02)
    app.include_router(upload.router, prefix="/upload", tags=["upload"])

    return app


# Instancia de la aplicacion lista para ser servida por uvicorn
app = create_app()
