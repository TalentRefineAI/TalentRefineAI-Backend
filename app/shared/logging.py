"""Configuracion centralizada de logging para los microservicios."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from shared.config import get_settings


def _add_app_env(app_env: str) -> Any:
    """Retorna un procesador para structlog que añade el entorno a los logs."""
    def processor(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Procesador interno de structlog para añadir la clave app_env al diccionario del evento."""
        event_dict["app_env"] = app_env
        return event_dict

    return processor


def _add_service_name(service_name: str):
    def processor(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        event_dict["service"] = service_name
        return event_dict

    return processor


def configure_logging(service_name: str) -> None:
    """Configura structlog para emitir logs en formato JSON en Azure o legibles en consola en local."""
    settings = get_settings()
    
    # Registrar nivel SUCCESS en el modulo de logging estandar si no existe
    success_level_num = 25
    if not hasattr(logging, "SUCCESS"):
        logging.SUCCESS = success_level_num
        logging.addLevelName(success_level_num, "SUCCESS")
        
        def success(self: logging.Logger, message: str, *args: Any, **kws: Any) -> None:
            """Registra un evento con nivel SUCCESS en el logger estándar."""
            if self.isEnabledFor(logging.SUCCESS):
                self._log(logging.SUCCESS, message, args, **kws)
        logging.Logger.success = success  # type: ignore

    # Registrar el metodo success en el BoundLogger de structlog
    import structlog.stdlib
    if not hasattr(structlog.stdlib.BoundLogger, "success"):
        def structlog_success(self: Any, event: Any = None, *args: Any, **kw: Any) -> Any:
            """Proxy para redirigir llamadas del metodo success de structlog al logger subyacente."""
            return self._proxy_to_logger("success", event, *args, **kw)
        structlog.stdlib.BoundLogger.success = structlog_success  # type: ignore

    # Registrar success en los diccionarios internos de structlog
    if "success" not in structlog.stdlib.NAME_TO_LEVEL:
        structlog.stdlib.NAME_TO_LEVEL["success"] = success_level_num
    if success_level_num not in structlog.stdlib.LEVEL_TO_NAME:
        structlog.stdlib.LEVEL_TO_NAME[success_level_num] = "success"

    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    renderer = (
        structlog.dev.ConsoleRenderer(colors=True)
        if settings.app_env == "local"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            _add_service_name(service_name),
            _add_app_env(settings.app_env),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
