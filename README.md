# TalentRefineAI-Backend

Backend del sistema **TalentRefine AI**, plataforma de reclutamiento inteligente que procesa CVs en PDF, los indexa en Qdrant con metadatos extraídos por IA, y responde consultas semánticas con candidatos ordenados por score híbrido (coseno + relevancia LLM).

---

## Estado del sistema

| Rama | Estado |
|------|--------|
| `dev` | Rama de desarrollo activa — **HEAD actual en producción** |
| `main` | Rama estable (merges desde `dev`) |

**Frontend en producción:**
`https://ca-frontend-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io`

---

## Arquitectura

Tres microservicios FastAPI independientes, cada uno con su propio Dockerfile:

```
app/
├── ingest_service/       # :8000 — Recibe PDFs y los sube a Azure Blob Storage
├── processing_service/   # :8002 — Extrae texto, metadatos LLM, chunking y vectoriza en Qdrant
├── rag_service/          # :8001 — Búsqueda semántica RAG + scoring híbrido LLM
└── shared/               # Configuración, modelos Pydantic, excepciones y fakes
```

### Flujo de ingesta (síncrono, serializado)

```
Frontend
  └─→ POST /upload (ingest-service)
        └─→ Azure Blob Storage (guarda PDF)
              └─→ POST /processing/{doc_id} (processing-service, espera respuesta)
                    ├─→ PyMuPDF + pypdf: extracción de texto completo
                    ├─→ GPT-4o-mini: extrae nombre, email, teléfono, ubicación,
                    │   años de experiencia y skills del CV completo
                    ├─→ Regex: refuerzo determinista de email y teléfono
                    ├─→ SentenceSplitter: chunking (1024 tokens, overlap 100)
                    ├─→ text-embedding-3-small: embeddings por chunk
                    └─→ Qdrant upsert: cada chunk lleva los metadatos del candidato
                        en su payload (denormalizados)
```

> **Diseño clave — procesamiento síncrono + semáforo:** el endpoint `/processing/{doc_id}` es síncrono (no usa BackgroundTasks). Un `asyncio.Semaphore(1)` serializa los CVs dentro de la réplica para no saturar los 10K TPM de Azure OpenAI. Azure Container Apps mantiene la réplica viva mientras hay una petición HTTP activa, lo que evita el problema de CVs quedados "en proceso" que ocurría con el enfoque async anterior (KEDA terminaba la réplica tras el 202).

### Flujo de búsqueda (síncrono)

```
Frontend
  └─→ POST /search (rag-service)
        ├─→ text-embedding-3-small: embed query
        ├─→ Qdrant query_points (top_k×3, sin filtro de location a nivel Qdrant)
        ├─→ Agrupa chunks por candidate_id → mejor coseno por candidato
        ├─→ GPT-4o-mini: extrae info + relevance_score 0-100 de todos los candidatos
        ├─→ Score híbrido = 0.4×coseno_normalizado + 0.6×(relevance_score/100)
        ├─→ Filtro post-LLM por exp_years (usa payload; fallback a LLM)
        ├─→ Filtro post-LLM por location (substring, case-insensitive)
        └─→ CandidateResult[] ordenados por score final
```

> **Filtro de location:** se aplica en Python post-retrieval con substring matching (`"Madrid" in "Madrid, Spain"`), no a nivel Qdrant, porque el campo es texto libre generado por LLM.

---

## Stack tecnológico

| Capa | Tecnología | Versión |
|------|-----------|---------|
| API | FastAPI + uvicorn | 0.136.1 / 0.46.0 |
| RAG | LlamaIndex | 0.14.21 |
| Vector store | Qdrant Cloud | v1.17.1 |
| Embeddings | Azure OpenAI `text-embedding-3-small` | dim 1536 |
| LLM | Azure OpenAI `gpt-4o-mini` | — |
| PDF storage | Azure Blob Storage | azure-storage-blob 12.28.0 |
| PDF parsing | PyMuPDF 1.26.0 + pypdf | — |
| Secrets | Azure Key Vault (DefaultAzureCredential) | — |
| Config | pydantic-settings | 2.14.1 |
| Logging | structlog | 25.5.0 |
| Containers | Docker / Azure Container Apps | — |

---

## Variables de entorno

| Variable | Descripción | Ejemplo |
|---|---|---|
| `AZURE_KEYVAULT_URL` | URL del Key Vault | `https://kv-talentrefine-dev.vault.azure.net/` |
| `QDRANT_URL` | URL de Qdrant | `https://xxx.qdrant.io` |
| `QDRANT_COLLECTION` | Nombre de colección | `talentrefine-candidates` |
| `AZURE_OPENAI_ENDPOINT` | Endpoint Azure OpenAI | `https://oai-xxx.openai.azure.com/` |
| `AZURE_OPENAI_API_VERSION` | Versión API | `2024-02-01` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Deployment de chat (extracción + búsqueda) | `gpt-4o-mini` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Deployment embedding | `text-embedding-3-small` |
| `AZURE_STORAGE_ACCOUNT_URL` | URL de Storage Account | `https://stxxx.blob.core.windows.net/` |
| `BLOB_CONTAINER_NAME` | Contenedor de blobs | `cv-uploads` |
| `USE_FAKE_INTEGRATIONS` | Modo fake local (sin Azure) | `false` |
| `INGEST_SERVICE_URL` | URL ingest (para health checks internos) | `http://ingest:8000` |
| `PROCESSING_SERVICE_URL` | URL processing (llamada desde ingest) | `http://processing:8002` |

Secretos en Azure Key Vault (vía `get_secret()`):
- `azure-openai-api-key`
- `qdrant-api-key`

---

## Desarrollo local (modo fake, sin Azure)

Requiere Docker Desktop.

```bash
git clone https://github.com/TalentRefineAI/TalentRefineAI-Backend.git
cd TalentRefineAI-Backend

# Levantar todos los servicios con fakes (sin credenciales Azure)
docker compose -f docker-compose.local-fake.yml up --build
```

Servicios disponibles:
- ingest-service:     http://localhost:8000/docs
- rag-service:        http://localhost:8001/docs
- processing-service: http://localhost:8002/docs
- Qdrant dashboard:   http://localhost:6333/dashboard

### Ejecutar tests

```bash
# Con servicios levantados (local-fake)
bash run_tests.sh

# O directamente
pytest tests/integration/ -v
```

---

## API reference

### ingest-service (`:8000`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/upload` | Sube PDFs (multipart); responde `{data: [{id, file_name, status}]}` |
| `GET` | `/upload/{doc_id}/status` | Estado de procesamiento del documento |
| `GET` | `/upload/{doc_id}/download` | Descarga PDF desde Blob Storage |
| `DELETE` | `/upload/all` | Elimina todos los blobs del contenedor |
| `GET` | `/health` | `{"status":"ok","service":"ingest-service"}` |

### rag-service (`:8001`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/search` | Búsqueda RAG; body: `SearchRequest` |
| `GET` | `/search/candidates` | Lista candidatos indexados (desde Qdrant) |
| `DELETE` | `/search/candidates` | Vacía la colección Qdrant |
| `GET` | `/health` | `{"status":"ok","service":"rag-service"}` |

**SearchRequest schema:**
```json
{
  "job_description": "string (min 10 chars)",
  "min_exp_years": 0,
  "required_location": null,
  "top_k": 5
}
```

**CandidateResult** (dentro de `SearchResponse.results`):
```json
{
  "candidate_id": "uuid",
  "file_name": "cv.pdf",
  "score": 0.82,
  "summary": "Resumen profesional generado por LLM.",
  "justification": "Motivo de encaje con el puesto generado por LLM.",
  "contact_info": {
    "name": "Ana García",
    "email": "ana@example.com",
    "phone": "+34 600 000 000",
    "skills": ["Python", "FastAPI", "Docker"]
  },
  "exp_years": 5,
  "location": "Madrid, Spain"
}
```

> `score` es el score híbrido 0-1. El frontend lo multiplica ×100 para mostrar %.
> `contact_info` y `location` se leen del payload Qdrant (extraídos en el procesamiento inicial), con fallback al LLM de búsqueda para CVs indexados antes del sistema de metadatos.

### processing-service (`:8002`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/processing/{doc_id}` | Procesa un CV: extrae texto + metadatos LLM + embeddings + upsert Qdrant. **Síncrono** — responde tras completar la indexación. |
| `GET` | `/health` | `{"status":"ok","service":"processing-service"}` |

### Payload por chunk en Qdrant

Cada chunk almacenado en Qdrant incluye los siguientes campos en su payload:

```json
{
  "candidate_id": "uuid",
  "file_name": "cv.pdf",
  "chunk_text": "texto del fragmento",
  "contact_info": {
    "name": "Ana García",
    "email": "ana@example.com",
    "phone": "+34 600 000 000",
    "skills": ["Python", "FastAPI"]
  },
  "exp_years": 5,
  "location": "Madrid, Spain"
}
```

Los metadatos (`contact_info`, `exp_years`, `location`) se extraen una sola vez del CV completo en el processing-service y se **denormalizan en todos los chunks**. Así la búsqueda vectorial siempre devuelve metadatos completos independientemente de qué fragmento tenga mayor similitud semántica con la query.

---

## Despliegue en producción

Ver **[OPERATIONS.md](OPERATIONS.md)** para todos los comandos de gestión, despliegue, mantenimiento, rollback y monitoreo del sistema en Azure.

---

## Flujo de trabajo Git

```
main  ← rama estable
 └── dev  ← rama de desarrollo activa (HEAD en producción)
```

Para desplegar cambios:
1. Commitar en `dev` y hacer push
2. Construir imagen en ACR: `az acr build ...`
3. Desplegar nueva revisión: `az containerapp update ...`

Ver OPERATIONS.md sección "Despliegue de nuevas versiones".

---

## Licencia

MIT
