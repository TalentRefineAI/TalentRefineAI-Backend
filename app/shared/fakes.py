"""Utilidades deterministas para ejecutar integraciones locales sin Azure."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from shared.config import get_settings


def local_blob_path(blob_name: str) -> Path:
    """Devuelve una ruta local segura para un nombre de blob logico."""
    base_path = Path(get_settings().local_blob_path)
    return base_path / blob_name


async def local_blob_upload(blob_name: str, data: bytes) -> str:
    """Persiste bytes en disco simulando Azure Blob Storage."""
    path = local_blob_path(blob_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path.as_uri()


async def local_blob_download(blob_name: str) -> bytes:
    """Lee bytes desde disco simulando la descarga de Azure Blob Storage."""
    return local_blob_path(blob_name).read_bytes()


def fake_embedding(text: str) -> list[float]:
    """
    Genera un vector determinista a partir de texto.

    No intenta modelar semantica real; solo da vectores estables para probar
    persistencia, busqueda y contratos del backend sin Azure OpenAI.
    """
    settings = get_settings()
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
    values: list[float] = []
    for idx in range(settings.fake_embedding_dimensions):
        byte = digest[idx % len(digest)]
        values.append((byte / 255.0) - 0.5)

    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]
