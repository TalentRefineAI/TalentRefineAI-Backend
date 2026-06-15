"""Middleware para el registro de solicitudes HTTP y medición de latencias."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware para interceptar peticiones HTTP, generar Request ID y registrar latencia en caso de error o lentitud."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Procesa la petición HTTP midiendo su tiempo de respuesta y registrando logs si supera umbrales o si falla."""
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start_time = time.perf_counter()
        status_code = 500
        error_msg = None
        error_type = None

        try:
            response = await call_next(request)
            status_code = response.status_code
            
            # Propagar X-Request-ID en la cabecera de la respuesta
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            status_code = 500
            error_msg = str(exc)
            error_type = type(exc).__name__
            raise exc
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            path = request.url.path
            
            # Umbrales de lentitud según la fase 3 del plan MVP
            is_slow = duration_ms >= 10000 or (path.startswith("/upload") and duration_ms >= 3000)
            client_host = request.client.host if request.client else None

            if status_code >= 500:
                logger.error(
                    "http_request_failed",
                    request_id=request_id,
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_host=client_host,
                    error=error_msg,
                    error_type=error_type,
                )
            elif status_code >= 400:
                logger.warning(
                    "http_request_warning",
                    request_id=request_id,
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_host=client_host,
                )
            elif is_slow:
                logger.warning(
                    "http_request_slow",
                    request_id=request_id,
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_host=client_host,
                )
