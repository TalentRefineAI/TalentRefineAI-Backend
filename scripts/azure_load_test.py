#!/usr/bin/env python3
"""
azure_load_test.py - Script de desgaste para analisis de costes en Azure Container Apps.

Simula el uso real de TalentRefineAI contra el ingest-service desplegado:
  - Upload de CVs ficticios        (POST /upload)
  - Polling de estado              (GET  /upload/{id}/status)

Nota: el rag-service tiene URL .internal. (solo accesible desde dentro del entorno
de Container Apps). Los costes RAG se incluyen como proyeccion teorica basada en
el volumen de busquedas diarias estimado.

Genera tres ficheros CSV y un informe Markdown con metricas de latencia
y estimacion de costes Azure OpenAI + Blob Storage + Container Apps.

Uso:
    python scripts/azure_load_test.py \
        --ingest-url https://ca-ingesta-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io \
        --duration   300                                               \
        --concurrent-users 3                                           \
        --pdfs-per-user    5                                           \
        --estimated-daily-searches 50                                  \
        --output-dir ./load_test_results
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import dataclasses
import datetime
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# ── Precios Azure (USD, junio 2026) ──────────────────────────────────────────
# text-embedding-3-small
_PRICE_EMBED_PER_1M = 0.02
# gpt-4o-mini
_PRICE_GPT_IN_PER_1M = 0.15
_PRICE_GPT_OUT_PER_1M = 0.60
# Azure Blob Storage (Hot LRS)
_PRICE_BLOB_WRITE_PER_10K = 0.065
_PRICE_BLOB_READ_PER_10K = 0.004
_PRICE_BLOB_GB_MONTH = 0.018
# Azure Container Apps
_PRICE_VCPU_PER_SEC = 0.000024
_PRICE_GIB_PER_SEC = 0.000003

# ── Parametros de estimacion de tokens (basados en el codigo real) ────────────
# processing-service: SentenceSplitter con chunk_size=1024
# Un CV de 2 paginas tiene ~2500-3500 tokens → ~3 chunks
_AVG_CHUNKS_PER_PDF = 3
_CHUNK_TOKENS = 1024

# processing-service: _extract_metadata → LLM con 3000 chars de texto (~750 tokens)
# Prompt de sistema ~200 tokens, JSON output ~200 tokens
_GPT_METADATA_IN_PER_PDF = 950
_GPT_METADATA_OUT_PER_PDF = 200

# rag-service: _search_azure → 1 embedding por query (proyeccion teorica)
_QUERY_EMBED_TOKENS = 55

# rag-service: _extract_all_candidates → 1 llamada LLM con top_k candidatos
# top_k=5, cada candidato tiene ~3 chunks × 400 tokens de contexto
# + prompt sistema ~600 tokens + descripcion puesto ~100 tokens
_GPT_SEARCH_IN_BASE = 700           # sistema + job_description
_GPT_SEARCH_IN_PER_CANDIDATE = 1200  # chunks de contexto por candidato
# JSON output: por candidato ~280 tokens (summary + justification + otros campos)
_GPT_SEARCH_OUT_PER_CANDIDATE = 280
_GPT_SEARCH_TOP_K = 5               # top_k por defecto en el rag-service

# ── Perfiles de CV ficticios ──────────────────────────────────────────────────
_CV_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "Ana Garcia Martinez",
        "title": "Senior Python Developer",
        "location": "Madrid",
        "exp_years": 8,
        "email": "ana.garcia@email.com",
        "phone": "+34 612 345 678",
        "skills": "Python, FastAPI, Django, PostgreSQL, Docker, Kubernetes, Azure, REST APIs, pytest",
        "experience": (
            "2019-Presente: Backend Developer en Telefonica. Microservicios con FastAPI y "
            "despliegue en Azure Container Apps. Liderazgo de migracion monolito a microservicios. "
            "Reduccion del tiempo de despliegue en un 60 por ciento mediante CI/CD con GitHub Actions. "
            "2016-2019: Software Engineer en BBVA. APIs REST con Python y Django. "
            "Integracion con sistemas bancarios legacy y auditoria de transacciones."
        ),
        "education": "Ingenieria Informatica, Universidad Politecnica de Madrid, 2016.",
    },
    {
        "name": "Carlos Lopez Fernandez",
        "title": "Data Engineer",
        "location": "Barcelona",
        "exp_years": 5,
        "email": "carlos.lopez@email.com",
        "phone": "+34 623 456 789",
        "skills": "Python, Spark, Databricks, Azure Data Factory, SQL, Kafka, Airflow, dbt, Pandas",
        "experience": (
            "2021-Presente: Data Engineer en Glovo. Pipeline ETL con Azure Data Factory y Databricks. "
            "Procesamiento de 100 millones de eventos diarios con Kafka y Spark Structured Streaming. "
            "Modelado dimensional con dbt y almacen de datos en Azure Synapse. "
            "2019-2021: Junior Data Engineer en Accenture. ETL con Python y SQL Server."
        ),
        "education": "Master en Big Data, Universidad de Barcelona, 2019.",
    },
    {
        "name": "Laura Sanchez Ruiz",
        "title": "Full Stack Developer",
        "location": "Valencia",
        "exp_years": 4,
        "email": "laura.sanchez@email.com",
        "phone": "+34 634 567 890",
        "skills": "React, TypeScript, Node.js, Python, MongoDB, Docker, AWS, GraphQL, Tailwind",
        "experience": (
            "2022-Presente: Full Stack Developer en Mango. Plataforma e-commerce con React 18 "
            "y Node.js. Migracion de REST a GraphQL. Reduccion de carga inicial en 40 por ciento. "
            "2020-2022: Frontend Developer en startup de logistica. SPA React con Redux y testing con Jest."
        ),
        "education": "Grado en Desarrollo de Software, UOC, 2020.",
    },
    {
        "name": "Pedro Martinez Gonzalez",
        "title": "DevOps Engineer",
        "location": "Bilbao",
        "exp_years": 6,
        "email": "pedro.martinez@email.com",
        "phone": "+34 645 678 901",
        "skills": "Kubernetes, Terraform, Azure, CI/CD, Jenkins, Prometheus, Grafana, Linux, Bash, Helm",
        "experience": (
            "2020-Presente: DevOps Engineer en Indra. Gestion de clusteres K8s en Azure AKS. "
            "Automatizacion IaC con Terraform y pipelines CI/CD con Azure DevOps. "
            "Monitorizacion con Prometheus y Grafana. SLA del 99.9 por ciento. "
            "2018-2020: SysAdmin en Capgemini. Administracion Linux y virtualizacion VMware."
        ),
        "education": "Ingenieria Tecnica en Telecomunicaciones, Universidad del Pais Vasco, 2018.",
    },
    {
        "name": "Maria Gonzalez Lopez",
        "title": "Machine Learning Engineer",
        "location": "Sevilla",
        "exp_years": 3,
        "email": "maria.gonzalez@email.com",
        "phone": "+34 656 789 012",
        "skills": "Python, TensorFlow, PyTorch, scikit-learn, MLflow, Azure ML, SQL, R, Hugging Face",
        "experience": (
            "2023-Presente: ML Engineer en NTT Data. Modelos de clasificacion y NLP para banca. "
            "Despliegue en Azure ML con MLflow tracking. Reduccion de falsos positivos en 35 por ciento. "
            "2022-2023: Data Scientist junior en consultora. Analisis exploratorio y modelos predictivos en Python."
        ),
        "education": "Grado en Estadistica, Universidad de Sevilla, 2022.",
    },
    {
        "name": "Javier Torres Medina",
        "title": "Backend Developer",
        "location": "Zaragoza",
        "exp_years": 7,
        "email": "javier.torres@email.com",
        "phone": "+34 667 890 123",
        "skills": "Java, Spring Boot, Python, FastAPI, PostgreSQL, Redis, RabbitMQ, Docker, OpenAPI",
        "experience": (
            "2019-Presente: Backend Developer en Repsol. APIs REST con Spring Boot y FastAPI. "
            "Integracion con sistemas ERP SAP. Diseno de arquitectura orientada a eventos con RabbitMQ. "
            "2017-2019: Desarrollador Java en IBM. Aplicaciones empresariales J2EE y servicios SOAP."
        ),
        "education": "Ingenieria Informatica, Universidad de Zaragoza, 2017.",
    },
    {
        "name": "Elena Romero Castro",
        "title": "Cloud Architect",
        "location": "Madrid",
        "exp_years": 10,
        "email": "elena.romero@email.com",
        "phone": "+34 678 901 234",
        "skills": "Azure, AWS, GCP, Terraform, Kubernetes, Networking, Security, Cost Optimization, AZ-305",
        "experience": (
            "2018-Presente: Cloud Architect en Microsoft Partner. Diseno de arquitecturas en Azure. "
            "Migraciones cloud, landing zones y optimizacion de costes. Certificada AZ-305 y AZ-900. "
            "Reduccion de costes del 30 por ciento en cliente enterprise mediante reserved instances. "
            "2014-2018: Infrastructure Engineer en Orange. Redes corporativas y virtualizacion."
        ),
        "education": "Ingenieria en Sistemas, Universidad Complutense de Madrid, 2014.",
    },
    {
        "name": "Roberto Silva Perez",
        "title": "Frontend Developer",
        "location": "Malaga",
        "exp_years": 5,
        "email": "roberto.silva@email.com",
        "phone": "+34 689 012 345",
        "skills": "Vue.js, React, JavaScript, TypeScript, CSS, Figma, Webpack, Jest, Cypress, Accessibility",
        "experience": (
            "2021-Presente: Frontend Developer en Booking.com. Componentes UI en Vue 3 con Composition API. "
            "Tests E2E con Cypress. Mejora del Core Web Vitals LCP en 50 por ciento. "
            "2019-2021: Maquetador web freelance. Proyectos para pymes y startups."
        ),
        "education": "Grado en Diseno Web, Universidad de Malaga, 2019.",
    },
    {
        "name": "Isabel Vega Moreno",
        "title": "QA Automation Engineer",
        "location": "Alicante",
        "exp_years": 6,
        "email": "isabel.vega@email.com",
        "phone": "+34 690 123 456",
        "skills": "Selenium, Playwright, Python, Java, Postman, JMeter, JIRA, Git, CI/CD, BDD, Gherkin",
        "experience": (
            "2020-Presente: QA Engineer en Amadeus. Testing funcional y de rendimiento para sistemas GDS. "
            "Automatizacion con Playwright y Python. Cobertura de regresion del 90 por ciento. "
            "2018-2020: Tester manual en empresa de turismo. Casos de prueba y ejecucion de regresion."
        ),
        "education": "FPII Desarrollo de Aplicaciones Multiplataforma, 2018.",
    },
    {
        "name": "Francisco Navarro Ruiz",
        "title": "Security Engineer",
        "location": "Bilbao",
        "exp_years": 9,
        "email": "francisco.navarro@email.com",
        "phone": "+34 601 234 567",
        "skills": "SIEM, OWASP, Pentesting, Azure Security Center, CSPM, Python, Splunk, Zero Trust, CISSP",
        "experience": (
            "2017-Presente: Security Engineer en Santander. SOC y SIEM con Splunk. "
            "Gestion de vulnerabilidades y respuesta a incidentes. CISSP y CEH certificado. "
            "Implementacion de arquitectura Zero Trust en entorno hibrido Azure. "
            "2015-2017: Analista de Seguridad en Deloitte. Auditorias y pentesting para banca y seguros."
        ),
        "education": "Ingenieria en Ciberseguridad, Universidad de Deusto, 2015.",
    },
]

# ── Descripciones de puestos para busquedas RAG ───────────────────────────────
_JOB_DESCRIPTIONS: list[str] = [
    "Buscamos Senior Python Developer con experiencia en FastAPI, microservicios y despliegue "
    "en Azure Container Apps. Minimo 5 anos de experiencia en backend con Python.",
    "Se busca Data Engineer con experiencia en pipelines ETL, Apache Spark, Azure Data Factory "
    "y procesamiento de grandes volumenes de datos en tiempo real con Kafka.",
    "Necesitamos Full Stack Developer con React y Node.js para plataforma e-commerce de alto "
    "trafico. Experiencia en TypeScript y bases de datos NoSQL como MongoDB.",
    "Buscamos DevOps Engineer para gestion de infraestructura en Kubernetes y automatizacion "
    "con Terraform en entorno Azure AKS. Experiencia con Prometheus y Grafana.",
    "Se requiere Machine Learning Engineer con experiencia en modelos NLP, Azure ML Studio "
    "y Python. Perfil de 2-4 anos con PyTorch o TensorFlow.",
    "Arquitecto Cloud con certificacion Azure AZ-305 para liderar migracion de sistemas "
    "on-premise a Azure. Experiencia en landing zones y optimizacion de costes cloud.",
    "Backend Developer Java o Python para integracion con sistemas ERP SAP y desarrollo "
    "de APIs REST de alta disponibilidad. Conocimientos de mensajeria con RabbitMQ.",
    "QA Automation Engineer con Playwright o Selenium y Python para equipo de testing "
    "de aplicaciones web criticas con cobertura de regresion superior al 85 por ciento.",
    "Security Engineer para equipo SOC con experiencia en SIEM Splunk, gestion de "
    "vulnerabilidades y seguridad en entornos Azure. Certificacion CISSP valorada.",
    "Frontend Developer Vue.js o React para startup fintech. Conocimientos de UX, "
    "accesibilidad WCAG y optimizacion de Core Web Vitals.",
]


# ── Modelos de datos ───────────────────────────────────────────────────────────

@dataclasses.dataclass
class OperationRecord:
    """Registro de una operacion individual realizada durante el test."""

    timestamp: str
    user_id: int
    operation: str       # upload | poll_status | search | list_candidates
    status_code: int
    latency_ms: float
    success: bool
    error: str
    doc_id: str
    extra: str           # JSON con datos adicionales de la operacion


@dataclasses.dataclass
class AggregatedMetrics:
    """Estadisticas descriptivas de latencia agrupadas por tipo de operacion."""

    operation: str
    count: int
    success: int
    errors: int
    success_rate_pct: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_avg_ms: float
    latency_min_ms: float
    latency_max_ms: float


@dataclasses.dataclass
class CostEstimate:
    """Estimacion de costes en USD para el periodo ejecutado."""

    # Consumo de tokens
    embed_tokens_total: int
    gpt_input_tokens_total: int
    gpt_output_tokens_total: int
    # Operaciones Blob
    blob_write_ops: int
    blob_read_ops: int
    blob_stored_gb: float
    # Costes por servicio
    cost_embedding_usd: float
    cost_gpt_input_usd: float
    cost_gpt_output_usd: float
    cost_blob_usd: float
    cost_container_apps_usd: float
    # Totales
    total_cost_usd: float
    monthly_extrapolation_usd: float
    # Contexto del test
    test_duration_seconds: float
    total_uploads: int
    estimated_daily_searches: int      # proyeccion teorica, no medida en tiempo real


# ── Generacion de PDFs ficticios ──────────────────────────────────────────────

def build_fake_pdf(candidate: dict[str, Any]) -> bytes:
    """Genera un PDF minimal con texto seleccionable a partir de los datos del candidato."""
    lines = [
        "CURRICULUM VITAE",
        "",
        f"Nombre: {candidate['name']}",
        f"Puesto: {candidate['title']}",
        f"Ubicacion: {candidate['location']}",
        f"Experiencia: {candidate['exp_years']} anos",
        f"Email: {candidate['email']}",
        f"Telefono: {candidate['phone']}",
        "",
        "HABILIDADES TECNICAS",
        candidate["skills"],
        "",
        "EXPERIENCIA PROFESIONAL",
        candidate["experience"],
        "",
        "FORMACION ACADEMICA",
        candidate["education"],
    ]
    cv_text = "\n".join(lines)

    stream = b"BT\n/F1 11 Tf\n50 750 Td\n14 TL\n"
    for line in cv_text.splitlines():
        encoded = line.encode("latin-1", errors="replace")
        encoded = encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        stream += b"(" + encoded + b") Tj T*\n"
    stream += b"ET\n"

    header = b"%PDF-1.4\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
    )
    obj4 = (
        b"4 0 obj\n<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"endstream\nendobj\n"
    )
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    body = obj1 + obj2 + obj3 + obj4 + obj5
    xref_offset = len(header) + len(body)

    offsets: list[int] = []
    pos = len(header)
    for obj in (obj1, obj2, obj3, obj4, obj5):
        offsets.append(pos)
        pos += len(obj)

    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n".encode()

    trailer = (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return header + body + xref + trailer


# ── Operaciones individuales ───────────────────────────────────────────────────

async def op_upload(
    client: httpx.AsyncClient,
    ingest_url: str,
    candidate: dict[str, Any],
    user_id: int,
) -> OperationRecord:
    """Sube un CV ficticio al ingest-service y registra la metrica de latencia."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pdf_bytes = build_fake_pdf(candidate)
    filename = f"cv_{candidate['name'].replace(' ', '_').lower()}.pdf"
    t0 = time.perf_counter()
    status_code = 0
    doc_id = ""
    error = ""
    success = False

    try:
        resp = await client.post(
            f"{ingest_url}/upload",
            files={"files": (filename, pdf_bytes, "application/pdf")},
        )
        status_code = resp.status_code
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        if status_code == 202:
            data = resp.json().get("data") or []
            if data:
                doc_id = data[0].get("id", "")
            success = True
        else:
            error = resp.text[:300]
    except Exception as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        error = str(exc)[:300]

    return OperationRecord(
        timestamp=ts,
        user_id=user_id,
        operation="upload",
        status_code=status_code,
        latency_ms=latency_ms,
        success=success,
        error=error,
        doc_id=doc_id,
        extra=json.dumps({"filename": filename, "size_bytes": len(pdf_bytes)}),
    )


async def op_poll_status(
    client: httpx.AsyncClient,
    ingest_url: str,
    doc_id: str,
    user_id: int,
    max_wait_sec: int = 180,
) -> OperationRecord:
    """Hace polling sobre GET /upload/{id}/status hasta completed, failed o timeout."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    t0 = time.perf_counter()
    attempts = 0
    final_status = "unknown"
    status_code = 0
    error = ""
    success = False
    deadline = time.time() + max_wait_sec

    while time.time() < deadline:
        attempts += 1
        try:
            resp = await client.get(f"{ingest_url}/upload/{doc_id}/status")
            status_code = resp.status_code
            if status_code == 200:
                final_status = (resp.json().get("data") or {}).get("status", "unknown")
                if final_status in ("completed", "failed"):
                    success = final_status == "completed"
                    break
        except Exception as exc:
            error = str(exc)[:300]
            break
        await asyncio.sleep(4)

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    return OperationRecord(
        timestamp=ts,
        user_id=user_id,
        operation="poll_status",
        status_code=status_code,
        latency_ms=latency_ms,
        success=success,
        error=error,
        doc_id=doc_id,
        extra=json.dumps({"final_status": final_status, "poll_attempts": attempts}),
    )


# ── Simulacion de usuario ──────────────────────────────────────────────────────

async def simulate_user(
    user_id: int,
    ingest_url: str,
    pdfs_per_user: int,
    semaphore: asyncio.Semaphore,
) -> list[OperationRecord]:
    """
    Simula el flujo de un reclutador contra el ingest-service (unico endpoint publico):
    sube CVs ficticios y hace polling hasta que el processing-service los completa.
    El rag-service es .internal. y solo es accesible desde dentro del entorno de
    Container Apps (el frontend lo llama directamente desde alli).
    """
    records: list[OperationRecord] = []
    timeout = httpx.Timeout(180.0, connect=30.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        pool = _CV_TEMPLATES.copy()
        random.shuffle(pool)
        candidates = pool[:pdfs_per_user] if pdfs_per_user <= len(pool) else (
            pool + random.choices(pool, k=pdfs_per_user - len(pool))
        )

        for candidate in candidates:
            async with semaphore:
                rec = await op_upload(client, ingest_url, candidate, user_id)
            records.append(rec)

            if rec.success and rec.doc_id:
                async with semaphore:
                    poll_rec = await op_poll_status(client, ingest_url, rec.doc_id, user_id)
                records.append(poll_rec)

            await asyncio.sleep(random.uniform(0.5, 2.0))

    return records


# ── Calculo de metricas ───────────────────────────────────────────────────────

def _percentile(data: list[float], p: float) -> float:
    """Calcula el percentil p (0-1) de una lista ordenada usando interpolacion lineal."""
    if not data:
        return 0.0
    k = (len(data) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    return data[f] if f == c else data[f] * (c - k) + data[c] * (k - f)


def aggregate_metrics(records: list[OperationRecord]) -> list[AggregatedMetrics]:
    """Calcula estadisticas descriptivas de latencia agrupadas por tipo de operacion."""
    by_op: dict[str, list[OperationRecord]] = {}
    for rec in records:
        by_op.setdefault(rec.operation, []).append(rec)

    result: list[AggregatedMetrics] = []
    for op, recs in sorted(by_op.items()):
        latencies = sorted(r.latency_ms for r in recs)
        successes = sum(1 for r in recs if r.success)
        count = len(recs)
        result.append(AggregatedMetrics(
            operation=op,
            count=count,
            success=successes,
            errors=count - successes,
            success_rate_pct=round(successes / count * 100, 1) if count else 0.0,
            latency_p50_ms=round(_percentile(latencies, 0.50), 1),
            latency_p95_ms=round(_percentile(latencies, 0.95), 1),
            latency_p99_ms=round(_percentile(latencies, 0.99), 1),
            latency_avg_ms=round(statistics.mean(latencies), 1) if latencies else 0.0,
            latency_min_ms=round(min(latencies), 1) if latencies else 0.0,
            latency_max_ms=round(max(latencies), 1) if latencies else 0.0,
        ))
    return result


def calculate_costs(
    records: list[OperationRecord],
    test_duration_sec: float,
    estimated_daily_searches: int,
) -> CostEstimate:
    """
    Estima el coste en USD basandose en las operaciones medidas (ingest) y en
    la proyeccion teorica de busquedas diarias (rag-service, interno).
    El modelo de tokens refleja el flujo real del codigo:
      - processing-service: 1 llamada LLM para metadatos + N embeddings por chunk.
      - rag-service: 1 embedding por query + 1 llamada LLM por busqueda (proyeccion).
    """
    uploads_ok = sum(1 for r in records if r.operation == "upload" and r.success)

    # Escala las busquedas estimadas al periodo del test para que la comparacion
    # con los uploads medidos sea coherente en la misma ventana de tiempo.
    searches_projected = int(estimated_daily_searches * test_duration_sec / 86400)

    # Tokens de embedding:
    #   - Procesamiento (medido): AVG_CHUNKS_PER_PDF × CHUNK_TOKENS por PDF subido
    #   - Busqueda (proyectado): QUERY_EMBED_TOKENS por query RAG
    embed_tokens = (
        uploads_ok * _AVG_CHUNKS_PER_PDF * _CHUNK_TOKENS
        + searches_projected * _QUERY_EMBED_TOKENS
    )

    # Tokens GPT input:
    #   - Procesamiento (medido): prompt metadata (~950 tokens) por PDF
    #   - Busqueda (proyectado): prompt base + top_k candidatos en contexto
    gpt_in_tokens = (
        uploads_ok * _GPT_METADATA_IN_PER_PDF
        + searches_projected * (_GPT_SEARCH_IN_BASE + _GPT_SEARCH_TOP_K * _GPT_SEARCH_IN_PER_CANDIDATE)
    )

    # Tokens GPT output:
    #   - Procesamiento (medido): JSON de metadatos (~200 tokens) por PDF
    #   - Busqueda (proyectado): JSON con summary + justification por candidato
    gpt_out_tokens = (
        uploads_ok * _GPT_METADATA_OUT_PER_PDF
        + searches_projected * _GPT_SEARCH_TOP_K * _GPT_SEARCH_OUT_PER_CANDIDATE
    )

    # Blob Storage: 1 write por upload exitoso, 1 read por polling de estado
    blob_writes = uploads_ok
    blob_reads = sum(1 for r in records if r.operation == "poll_status")
    blob_stored_gb = uploads_ok * 0.0002  # ~200 KB por PDF

    # Costes Azure OpenAI
    cost_embed = (embed_tokens / 1_000_000) * _PRICE_EMBED_PER_1M
    cost_gpt_in = (gpt_in_tokens / 1_000_000) * _PRICE_GPT_IN_PER_1M
    cost_gpt_out = (gpt_out_tokens / 1_000_000) * _PRICE_GPT_OUT_PER_1M

    # Coste Blob Storage (prorrateo diario del almacenamiento)
    cost_blob = (
        (blob_writes / 10_000) * _PRICE_BLOB_WRITE_PER_10K
        + (blob_reads / 10_000) * _PRICE_BLOB_READ_PER_10K
        + blob_stored_gb * _PRICE_BLOB_GB_MONTH / 30
    )

    # Coste Container Apps: 3 instancias (ingest + processing + rag)
    # con 0.5 vCPU y 1 GiB cada una durante la duracion del test
    vcpu_sec = 3 * 0.5 * test_duration_sec
    gib_sec = 3 * 1.0 * test_duration_sec
    cost_ca = vcpu_sec * _PRICE_VCPU_PER_SEC + gib_sec * _PRICE_GIB_PER_SEC

    total = cost_embed + cost_gpt_in + cost_gpt_out + cost_blob + cost_ca

    # Extrapolacion mensual: patron de uso de 8h/dia durante 22 dias laborables
    scale = (8 * 3600 * 22) / test_duration_sec if test_duration_sec > 0 else 0.0
    monthly = total * scale

    return CostEstimate(
        embed_tokens_total=embed_tokens,
        gpt_input_tokens_total=gpt_in_tokens,
        gpt_output_tokens_total=gpt_out_tokens,
        blob_write_ops=blob_writes,
        blob_read_ops=blob_reads,
        blob_stored_gb=round(blob_stored_gb, 6),
        cost_embedding_usd=round(cost_embed, 6),
        cost_gpt_input_usd=round(cost_gpt_in, 6),
        cost_gpt_output_usd=round(cost_gpt_out, 6),
        cost_blob_usd=round(cost_blob, 6),
        cost_container_apps_usd=round(cost_ca, 6),
        total_cost_usd=round(total, 6),
        monthly_extrapolation_usd=round(monthly, 4),
        test_duration_seconds=round(test_duration_sec, 2),
        total_uploads=uploads_ok,
        estimated_daily_searches=estimated_daily_searches,
    )


# ── Exportacion de resultados ─────────────────────────────────────────────────

def export_csv(
    records: list[OperationRecord],
    metrics: list[AggregatedMetrics],
    cost: CostEstimate,
    output_dir: Path,
    timestamp: str,
) -> tuple[Path, Path, Path]:
    """Exporta tres ficheros CSV: operaciones detalladas, metricas agregadas y costes."""
    ops_path = output_dir / f"load_test_operations_{timestamp}.csv"
    with ops_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [field.name for field in dataclasses.fields(OperationRecord)]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            writer.writerow(dataclasses.asdict(rec))

    metrics_path = output_dir / f"load_test_metrics_{timestamp}.csv"
    with metrics_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [field.name for field in dataclasses.fields(AggregatedMetrics)]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in metrics:
            writer.writerow(dataclasses.asdict(m))

    costs_path = output_dir / f"load_test_costs_{timestamp}.csv"
    with costs_path.open("w", newline="", encoding="utf-8") as f:
        writer_plain = csv.writer(f)
        writer_plain.writerow(["metrica", "valor"])
        for field in dataclasses.fields(CostEstimate):
            writer_plain.writerow([field.name, getattr(cost, field.name)])

    return ops_path, metrics_path, costs_path


def export_markdown(
    records: list[OperationRecord],
    metrics: list[AggregatedMetrics],
    cost: CostEstimate,
    config: dict[str, Any],
    output_dir: Path,
    timestamp: str,
) -> Path:
    """Genera el informe completo de carga y costes en formato Markdown."""
    md_path = output_dir / f"load_test_report_{timestamp}.md"
    run_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_ops = len(records)
    total_ok = sum(1 for r in records if r.success)
    global_ok_pct = round(total_ok / total_ops * 100, 1) if total_ops else 0.0

    errors_by_op: dict[str, list[str]] = {}
    for rec in records:
        if not rec.success and rec.error:
            errors_by_op.setdefault(rec.operation, []).append(rec.error)

    lines: list[str] = [
        "# TalentRefineAI – Informe de Carga Azure",
        "",
        f"> Generado el {run_ts}",
        "",
        "## 1. Configuracion del test",
        "",
        "| Parametro | Valor |",
        "|---|---|",
        f"| Ingest URL | `{config['ingest_url']}` |",
        "| RAG URL | `.internal.` (solo accesible desde dentro del entorno de Container Apps) |",
        f"| Duracion configurada | {config['duration']}s |",
        f"| Duracion real | {cost.test_duration_seconds}s |",
        f"| Usuarios concurrentes | {config['concurrent_users']} |",
        f"| PDFs por usuario | {config['pdfs_per_user']} |",
        f"| Busquedas RAG diarias (proyeccion) | {config['estimated_daily_searches']} |",
        f"| Modelo embedding | text-embedding-3-small (configurado en el servicio) |",
        f"| Modelo chat | gpt-4o-mini (configurado en el servicio) |",
        "",
        "## 2. Resumen de operaciones",
        "",
        "| Metrica | Valor |",
        "|---|---|",
        f"| Total operaciones | {total_ops} |",
        f"| Operaciones exitosas | {total_ok} |",
        f"| Operaciones fallidas | {total_ops - total_ok} |",
        f"| Tasa de exito global | {global_ok_pct}% |",
        f"| CVs subidos y procesados | {cost.total_uploads} |",
        f"| Busquedas RAG diarias (proyeccion) | {cost.estimated_daily_searches} |",
        "",
        "## 3. Metricas de latencia por operacion",
        "",
        "| Operacion | N | Exito% | p50 (ms) | p95 (ms) | p99 (ms) | Avg (ms) | Min (ms) | Max (ms) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for m in metrics:
        lines.append(
            f"| {m.operation} | {m.count} | {m.success_rate_pct}% "
            f"| {m.latency_p50_ms} | {m.latency_p95_ms} | {m.latency_p99_ms} "
            f"| {m.latency_avg_ms} | {m.latency_min_ms} | {m.latency_max_ms} |"
        )

    lines += [
        "",
        "> **Nota**: `poll_status` incluye el tiempo total de espera hasta que el "
        "processing-service devuelve `completed` (extraccion de texto + embeddings + Qdrant).",
        "",
        "## 4. Estimacion de costes (USD)",
        "",
        "### 4.1 Consumo de tokens Azure OpenAI",
        "",
        "> Modelo de estimacion:",
        "> - **Processing** (medido en el test): 1 llamada LLM para extraccion de metadatos (~950 tokens input)",
        f">   + {_AVG_CHUNKS_PER_PDF} embeddings x {_CHUNK_TOKENS} tokens/chunk por PDF subido.",
        "> - **Search RAG** (proyeccion teorica): el rag-service tiene URL .internal. y solo",
        ">   es accesible desde dentro del entorno de Container Apps. Los costes se proyectan",
        f">   a partir de {cost.estimated_daily_searches} busquedas/dia configuradas: 1 embedding (~55 tokens) + 1 llamada LLM",
        f">   con top-{_GPT_SEARCH_TOP_K} candidatos en contexto por busqueda.",
        "",
        "| Concepto | Tokens estimados | Coste (USD) |",
        "|---|---|---|",
        f"| Embeddings (procesamiento + queries) | {cost.embed_tokens_total:,} | ${cost.cost_embedding_usd:.6f} |",
        f"| GPT input (metadatos + contexto RAG) | {cost.gpt_input_tokens_total:,} | ${cost.cost_gpt_input_usd:.6f} |",
        f"| GPT output (JSON metadatos + summaries) | {cost.gpt_output_tokens_total:,} | ${cost.cost_gpt_output_usd:.6f} |",
        "",
        "### 4.2 Azure Blob Storage",
        "",
        "| Concepto | Cantidad | Coste (USD) |",
        "|---|---|---|",
        f"| Operaciones de escritura (uploads) | {cost.blob_write_ops} | incl. en total |",
        f"| Operaciones de lectura (polling) | {cost.blob_read_ops} | incl. en total |",
        f"| Almacenamiento estimado | {cost.blob_stored_gb:.4f} GB | incl. en total |",
        f"| **Total Blob Storage** | — | **${cost.cost_blob_usd:.6f}** |",
        "",
        "### 4.3 Azure Container Apps",
        "",
        "| Concepto | Detalle |",
        "|---|---|",
        "| Instancias activas | 3 (ingest-service, processing-service, rag-service) |",
        "| vCPU por instancia (estimada) | 0.5 vCPU |",
        "| Memoria por instancia (estimada) | 1 GiB |",
        f"| Duracion activa | {cost.test_duration_seconds}s |",
        f"| **Coste Container Apps** | **${cost.cost_container_apps_usd:.6f}** |",
        "",
        "### 4.4 Resumen total y extrapolacion mensual",
        "",
        "| Concepto | Coste (USD) |",
        "|---|---|",
        f"| Azure OpenAI – Embeddings | ${cost.cost_embedding_usd:.6f} |",
        f"| Azure OpenAI – GPT input | ${cost.cost_gpt_input_usd:.6f} |",
        f"| Azure OpenAI – GPT output | ${cost.cost_gpt_output_usd:.6f} |",
        f"| Azure Blob Storage | ${cost.cost_blob_usd:.6f} |",
        f"| Azure Container Apps | ${cost.cost_container_apps_usd:.6f} |",
        f"| **TOTAL del test** | **${cost.total_cost_usd:.6f}** |",
        f"| **Extrapolacion mensual** *(8h/dia x 22 dias laborables)* | **${cost.monthly_extrapolation_usd:.4f}** |",
        "",
        "> **Aviso**: Los tokens son estimaciones basadas en el comportamiento observado del codigo.",
        "> Los costes reales pueden variar segun el contenido de los CVs y las respuestas del LLM.",
        "> Precios usados (junio 2026): embedding $0.02/1M, GPT-4o-mini input $0.15/1M, output $0.60/1M.",
        "> Container Apps: $0.000024/vCPU-segundo, $0.000003/GiB-segundo.",
        "",
    ]

    if errors_by_op:
        lines += ["## 5. Errores detectados", ""]
        for op, errs in sorted(errors_by_op.items()):
            lines.append(f"### {op}")
            lines.append("")
            for err in list(dict.fromkeys(errs))[:5]:  # top 5 errores unicos
                lines.append(f"- `{err}`")
            lines.append("")
    else:
        lines += ["## 5. Errores detectados", "", "Sin errores registrados.", ""]

    lines += [
        "## 6. Archivos generados",
        "",
        "| Archivo | Contenido |",
        "|---|---|",
        "| `load_test_operations_*.csv` | Registro detallado de cada operacion (timestamp, latencia, status) |",
        "| `load_test_metrics_*.csv` | Percentiles y estadisticas agrupadas por tipo de operacion |",
        "| `load_test_costs_*.csv` | Desglose de tokens y costes estimados |",
        "| `load_test_report_*.md` | Este informe |",
        "",
        "---",
        "*Script: `scripts/azure_load_test.py` – TalentRefineAI Backend*",
    ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


# ── Health check ──────────────────────────────────────────────────────────────

async def check_services_health(ingest_url: str) -> None:
    """Verifica que el ingest-service (unico endpoint publico) este accesible."""
    url = f"{ingest_url}/health"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                print(f"  [OK] ingest-service ({url})")
            else:
                print(f"  [WARN] ingest-service respondio {resp.status_code} ({url})")
        except Exception as exc:
            print(f"  [ERROR] ingest-service no accesible: {exc}", file=sys.stderr)
            sys.exit(1)
    print("  [INFO] rag-service (.internal.) no se verifica desde fuera del entorno")


# ── Orquestador principal ─────────────────────────────────────────────────────

async def run_load_test(config: dict[str, Any]) -> None:
    """Orquesta el test completo: health check, simulacion concurrente e informes."""
    print("Verificando accesibilidad de los servicios...")
    await check_services_health(config["ingest_url"])
    print()

    print(
        f"Iniciando test de carga en Azure\n"
        f"  Duracion maxima           : {config['duration']}s\n"
        f"  Usuarios concurrentes     : {config['concurrent_users']}\n"
        f"  PDFs / usuario            : {config['pdfs_per_user']}\n"
        f"  Busquedas/dia (proyeccion): {config['estimated_daily_searches']}\n"
    )

    semaphore = asyncio.Semaphore(config["concurrent_users"])
    t_start = time.perf_counter()

    tasks = [
        asyncio.create_task(
            simulate_user(
                user_id=i,
                ingest_url=config["ingest_url"],
                pdfs_per_user=config["pdfs_per_user"],
                semaphore=semaphore,
            )
        )
        for i in range(config["concurrent_users"])
    ]

    done, pending = await asyncio.wait(tasks, timeout=config["duration"])

    if pending:
        print(f"Timeout alcanzado ({config['duration']}s). Cancelando {len(pending)} tarea(s) pendiente(s).")
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    test_duration = time.perf_counter() - t_start
    records: list[OperationRecord] = []
    for task in done:
        try:
            records.extend(task.result())
        except Exception as exc:
            print(f"  [WARN] Tarea de usuario fallo: {exc}", file=sys.stderr)

    print(f"Test completado en {test_duration:.1f}s | {len(records)} operaciones registradas.")

    metrics = aggregate_metrics(records)
    cost = calculate_costs(records, test_duration, config["estimated_daily_searches"])

    ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    ops_path, metrics_path, costs_path = export_csv(records, metrics, cost, output_dir, ts)
    md_path = export_markdown(records, metrics, cost, config, output_dir, ts)

    # Resumen en consola
    print()
    print("=" * 65)
    print("RESUMEN DE LATENCIAS")
    print("=" * 65)
    col = "{:<22} {:>5} {:>8} {:>10} {:>10}"
    print(col.format("Operacion", "N", "Exito%", "p50 (ms)", "p95 (ms)"))
    print("-" * 65)
    for m in metrics:
        print(col.format(
            m.operation,
            m.count,
            f"{m.success_rate_pct}%",
            f"{m.latency_p50_ms}",
            f"{m.latency_p95_ms}",
        ))

    print()
    print("=" * 65)
    print("ESTIMACION DE COSTES")
    print("=" * 65)
    print(f"  CVs procesados            : {cost.total_uploads}")
    print(f"  Busquedas/dia (proyeccion): {cost.estimated_daily_searches}")
    print(f"  Tokens embedding          : {cost.embed_tokens_total:,}")
    print(f"  Tokens GPT (input)        : {cost.gpt_input_tokens_total:,}")
    print(f"  Tokens GPT (output)       : {cost.gpt_output_tokens_total:,}")
    print(f"  Coste total del test      : ${cost.total_cost_usd:.6f}")
    print(f"  Extrapolacion mensual     : ${cost.monthly_extrapolation_usd:.4f}")
    print()
    print("Informes generados:")
    print(f"  {ops_path}")
    print(f"  {metrics_path}")
    print(f"  {costs_path}")
    print(f"  {md_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Script de desgaste para analisis de costes en Azure – TalentRefineAI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ingest-url",
        required=True,
        help="URL base del ingest-service en Azure Container Apps (sin barra final)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="Duracion maxima del test en segundos (actua como timeout global)",
    )
    parser.add_argument(
        "--concurrent-users",
        type=int,
        default=3,
        help="Numero de reclutadores simulados en paralelo",
    )
    parser.add_argument(
        "--pdfs-per-user",
        type=int,
        default=5,
        help="CVs ficticios a subir y procesar por usuario",
    )
    parser.add_argument(
        "--estimated-daily-searches",
        type=int,
        default=50,
        help="Numero estimado de busquedas RAG diarias (proyeccion teorica de costes)",
    )
    parser.add_argument(
        "--output-dir",
        default="./load_test_results",
        help="Directorio donde se guardan los informes CSV y Markdown",
    )

    args = parser.parse_args()
    config: dict[str, Any] = {
        "ingest_url": args.ingest_url.rstrip("/"),
        "duration": args.duration,
        "concurrent_users": args.concurrent_users,
        "pdfs_per_user": args.pdfs_per_user,
        "estimated_daily_searches": args.estimated_daily_searches,
        "output_dir": args.output_dir,
    }

    asyncio.run(run_load_test(config))


if __name__ == "__main__":
    main()
