"""Configuracion centralizada de logging para los microservicios."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from shared.config import get_settings


def _add_service_name(service_name: str):
    def processor(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        event_dict["service"] = service_name
        return event_dict

    return processor


def configure_logging(service_name: str) -> None:
    """Configura structlog para emitir logs trazables por stdout."""
    settings = get_settings()
    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            _add_service_name(service_name),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
