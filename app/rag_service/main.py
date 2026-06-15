"""
rag-service – Punto de entrada FastAPI.
Resuelve consultas de reclutadores: embedding → prefiltrado → búsqueda vectorial → GPT-4.
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.config import get_settings
from shared.logging import configure_logging
from shared.request_logging import RequestLoggingMiddleware

from .routers import search

configure_logging("rag-service")
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Acciones de inicio y parada del servicio
    logger.info("rag-service starting")
    yield
    logger.info("rag-service shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="TalentRefine – RAG Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(RequestLoggingMiddleware)

    # Permite peticiones desde el frontend Streamlit
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Registra el router de busqueda bajo el prefijo /search

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "rag-service"}

    app.include_router(search.router, prefix="/search", tags=["search"])

    return app


# Instancia lista para ser servida por uvicorn
app = create_app()
