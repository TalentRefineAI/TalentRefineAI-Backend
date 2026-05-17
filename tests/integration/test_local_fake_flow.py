"""
Tests de integracion del flujo completo en modo fake (sin Azure).

Prerequisitos:
    docker compose -f docker-compose.yml -f docker-compose.local-fake.yml up -d

Ejecucion:
    pytest tests/integration/ -v

Variables de entorno opcionales:
    INGEST_URL    (default: http://localhost:8001)
    PROCESSING_URL (default: http://localhost:8002)
    RAG_URL       (default: http://localhost:8003)
"""
import io
import os
import time

import httpx
import pytest
from reportlab.pdfgen import canvas  # type: ignore[import]

INGEST_URL = os.getenv("INGEST_URL", "http://localhost:8001")
PROCESSING_URL = os.getenv("PROCESSING_URL", "http://localhost:8002")
RAG_URL = os.getenv("RAG_URL", "http://localhost:8003")

TIMEOUT = 15.0


def _make_pdf(text: str) -> bytes:
    """Crea un PDF minimo en memoria con el texto dado."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 750, text)
    c.save()
    return buf.getvalue()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(timeout=TIMEOUT) as c:
        yield c


@pytest.fixture(scope="module")
def sample_pdf() -> bytes:
    return _make_pdf(
        "Senior Python Engineer. 8 years experience. "
        "Skills: FastAPI, Docker, Qdrant, LLM. Location: Madrid."
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestHealthChecks:
    """Los tres servicios deben responder en /health o en /."""

    @pytest.mark.parametrize("url", [INGEST_URL, PROCESSING_URL, RAG_URL])
    def test_service_reachable(self, client: httpx.Client, url: str) -> None:
        # Cualquier respuesta no-5xx indica que el servicio esta en pie
        r = client.get(url + "/docs")
        assert r.status_code < 500, f"{url} devolvio {r.status_code}"


class TestIngestService:
    """Subida de PDF al ingest-service."""

    def test_upload_pdf(self, client: httpx.Client, sample_pdf: bytes) -> None:
        r = client.post(
            f"{INGEST_URL}/upload",
            files={"files": ("cv_test.pdf", sample_pdf, "application/pdf")},
        )
        assert r.status_code in (200, 201, 202), r.text
        body = r.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert body["data"]
        assert "id" in body["data"][0] or "doc_id" in body["data"][0]


class TestFullFlow:
    """Flujo completo: upload -> processing -> search."""

    def test_upload_process_search(self, client: httpx.Client, sample_pdf: bytes) -> None:
        # 1. Upload
        r = client.post(
            f"{INGEST_URL}/upload",
            files={"files": ("cv_senior.pdf", sample_pdf, "application/pdf")},
        )
        assert r.status_code in (200, 201, 202), f"Upload failed: {r.text}"
        upload_data = r.json()["data"][0]
        doc_id = upload_data.get("id") or upload_data.get("doc_id")
        blob_name = f"{doc_id}/cv_senior.pdf"

        # 2. Trigger processing (sincrono en test; el router responde 202)
        r2 = client.post(
            f"{PROCESSING_URL}/processing/{doc_id}",
            params={"blob_name": blob_name},
        )
        assert r2.status_code == 202, f"Processing trigger failed: {r2.text}"

        # 3. Esperar a que el background task indexe (fake mode es rapido)
        time.sleep(3)

        # 4. Search
        r3 = client.post(
            f"{RAG_URL}/search",
            json={
                "job_description": "Python engineer with FastAPI and Docker experience for backend role",
                "top_k": 3,
            },
        )
        assert r3.status_code == 200, f"Search failed: {r3.text}"
        body = r3.json()
        assert "results" in body
        assert isinstance(body["results"], list)

    def test_search_with_filters(self, client: httpx.Client) -> None:
        """El endpoint de search acepta filtros opcionales sin error."""
        r = client.post(
            f"{RAG_URL}/search",
            json={
                "job_description": "DevOps engineer with Kubernetes",
                "min_exp_years": 3.0,
                "required_location": "Madrid",
                "top_k": 5,
            },
        )
        assert r.status_code == 200, r.text
        assert "results" in r.json()

    def test_search_schema_validation(self, client: httpx.Client) -> None:
        """job_description demasiado corto debe devolver 422."""
        r = client.post(
            f"{RAG_URL}/search",
            json={"job_description": "short"},
        )
        assert r.status_code == 422
