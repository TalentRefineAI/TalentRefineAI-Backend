"""Adaptadores fake para desarrollo local sin Azure."""
from __future__ import annotations

import hashlib
import math
import pathlib

from shared.config import get_settings


def _blob_root() -> pathlib.Path:
    p = pathlib.Path(get_settings().local_blob_path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def local_blob_upload(blob_name: str, data: bytes) -> str:
    """Guarda el blob en disco local. Devuelve la ruta como URL fake."""
    dest = _blob_root() / blob_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return f"file://{dest}"


def local_blob_download(blob_name: str) -> bytes:
    """Lee el blob desde disco local."""
    dest = _blob_root() / blob_name
    if not dest.exists():
        raise FileNotFoundError(f"Blob local no encontrado: {blob_name}")
    return dest.read_bytes()


def fake_embedding(text: str, dims: int | None = None) -> list[float]:
    """
    Genera un vector de embedding deterministico a partir del texto usando SHA-256.
    El resultado es siempre el mismo para el mismo texto y esta normalizado (norma L2 = 1).
    """
    if dims is None:
        dims = get_settings().fake_embedding_dimensions
    # Rellenar dims floats a partir de los bytes del hash, repitiendo si hace falta
    digest = hashlib.sha256(text.encode("utf-8")).digest()  # 32 bytes
    # Expandir a dims valores en [-1, 1]
    extended: list[float] = []
    i = 0
    while len(extended) < dims:
        b = digest[i % len(digest)]
        # Segundo byte para mas variacion
        b2 = digest[(i + 1) % len(digest)]
        val = ((b * 256 + b2) / 32767.5) - 1.0  # rango [-1, 1]
        extended.append(val)
        i += 1
    # Normalizar a norma L2 = 1 (obligatorio para similitud coseno)
    norm = math.sqrt(sum(v * v for v in extended)) or 1.0
    return [v / norm for v in extended]
