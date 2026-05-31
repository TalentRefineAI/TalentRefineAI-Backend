# TalentRefineAI-Backend

Backend del sistema **TalentRefine AI**, plataforma de reclutamiento inteligente que permite buscar candidatos mediante lenguaje natural. Procesa CVs en PDF, los indexa en Qdrant y responde consultas con candidatos ordenados por relevancia, resumen y justificación generados por GPT-4o-mini.

---

## Estado del sistema

| Rama | Estado |
|------|--------|
| `dev` | ✅ Producción desplegada en Azure Container Apps |
| `main` | Rama estable (merges desde `dev`) |

**URL del frontend en producción:**
`https://ca-frontend-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io`

---

## Arquitectura

Tres microservicios FastAPI independientes, cada uno con su propio Dockerfile:

```
app/
├── ingest_service/       # :8000 — Recibe PDFs y los sube a Azure Blob Storage
├── processing_service/   # :8002 — Extrae texto, chunking y vectoriza en Qdrant
├── rag_service/          # :8001 — Búsqueda semántica RAG + scoring híbrido LLM
└── shared/               # Configuración, modelos Pydantic, excepciones y fakes
```

### Flujo de ingesta (background task)

```
Frontend
  └─→ POST /upload (ingest-service)
        └─→ Azure Blob Storage (guarda PDF)
              └─→ background task → POST /processing/{doc_id} (processing-service)
                    ├─→ PyMuPDF extracción de texto
                    ├─→ SentenceSplitter chunking
                    ├─→ text-embedding-3-small (Azure OpenAI)
                    └─→ Qdrant upsert (colección talentrefine-candidates)
```

### Flujo de búsqueda (síncrono)

```
Frontend
  └─→ POST /search (rag-service)
        ├─→ Embed query (text-embedding-3-small)
        ├─→ Qdrant top-k×3 con MetadataFilters
        ├─→ Group by candidate_id → mejor coseno por candidato
        ├─→ GPT-4o-mini: extrae info + relevance_score 0-100
        ├─→ Score híbrido = 0.4×coseno_normalizado + 0.6×(relevance_score/100)
        └─→ CandidateResult[] ordenados por score final
```

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
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Deployment de chat | `gpt-4o-mini` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Deployment embedding | `text-embedding-3-small` |
| `AZURE_STORAGE_ACCOUNT_URL` | URL de Storage Account | `https://stxxx.blob.core.windows.net/` |
| `BLOB_CONTAINER_NAME` | Contenedor de blobs | `cv-uploads` |
| `USE_FAKE_INTEGRATIONS` | Modo fake local | `false` |
| `INGEST_SERVICE_URL` | URL ingest (para processing) | `http://ingest:8000` |

Secretos almacenados en Azure Key Vault (vía `get_secret()`):
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
| `POST` | `/upload` | Sube PDFs; responde `{data: [{id, file_name, status}]}` |
| `GET` | `/upload/{doc_id}/status` | Estado de procesamiento del documento |
| `GET` | `/upload/{doc_id}/download` | Descarga PDF |
| `DELETE` | `/upload/all` | Elimina todos los blobs del contenedor |
| `GET` | `/health` | `{"status":"ok","service":"ingest-service"}` |

### rag-service (`:8001`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/search` | Búsqueda RAG; body: `SearchRequest` |
| `GET` | `/search/candidates` | Lista candidatos indexados |
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
  "summary": "Resumen en 2-3 frases generado por LLM.",
  "justification": "Motivo de encaje generado por LLM.",
  "contact_info": {"name": "...", "email": "...", "phone": "...", "skills": []},
  "exp_years": 5,
  "location": "Madrid"
}
```

> `score` = score híbrido 0-1. El frontend lo multiplica ×100 para mostrar %.

### processing-service (`:8002`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/processing/{doc_id}` | Extrae texto + embedding + upsert Qdrant |
| `GET` | `/health` | `{"status":"ok","service":"processing-service"}` |

---

## Despliegue en producción

Ver **[OPERATIONS.md](OPERATIONS.md)** para todos los comandos de gestión, despliegue,
mantenimiento, rollback y monitoreo del sistema en Azure.

---

## Flujo de trabajo Git

```
main  ← rama estable
 └── dev  ← rama de desarrollo activa (actual HEAD producción)
```

Para desplegar cambios:
1. Commitar en `dev` y hacer push
2. Construir imagen en ACR: `az acr build ...`
3. Desplegar nueva revisión: `az containerapp update ...`

Ver OPERATIONS.md sección "Despliegue de nuevas versiones".

---

## Licencia

MIT
