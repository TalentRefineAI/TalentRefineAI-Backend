# TalentRefineAI-Backend

Backend del sistema **TalentRefine AI**, una plataforma de reclutamiento inteligente que permite a los reclutadores buscar candidatos mediante lenguaje natural. El sistema procesa CVs en formato PDF, los indexa en una base de datos vectorial y responde consultas con candidatos ordenados por relevancia, acompañados de un resumen y justificacion generados por GPT-4o-mini.

---

## Estado del repositorio

| Rama | Estado |
|------|--------|
| `feat/ia-rag-devops` | Motor RAG completo + integracion DevOps **lista para PR** |
| `dev` | Base estable, sin motor RAG |
| `main` | Rama por defecto del repositorio |

La rama `feat/ia-rag-devops` integra el trabajo de dos frentes paralelos:

- **Frente 2 (IA):** motor RAG completo con LlamaIndex, pipeline de indexacion PDF->Qdrant, modo fake local sin Azure, 28 tests unitarios y 7 tests de integracion E2E.
- **Frente 4 (DevOps):** red Docker explicita (`talentrefine-local`), healthchecks en todos los servicios, dependencias por servicio en `requirements/`, `.dockerignore` y estructura de despliegue multi-stage.

---

## Arquitectura

El backend esta compuesto por tres microservicios FastAPI independientes:

```
app/
+-- ingest_service/        # Recibe PDFs, valida y sube a Azure Blob Storage
+-- processing_service/    # Extrae texto, hace chunking y persiste vectores en Qdrant
+-- rag_service/           # Resuelve consultas RAG y devuelve candidatos con score
`-- shared/                # Configuracion, modelos, excepciones y adaptadores fake
```

### Flujo de ingesta (asincrono)

```
Frontend --> POST /upload --> ingest-service --> Azure Blob Storage
                                             --> (background task) --> processing-service
                                                                    --> Extraccion PDF (PyMuPDF)
                                                                    --> Chunking (LlamaIndex SentenceSplitter)
                                                                    --> Embedding (Azure OpenAI text-embedding-3-small)
                                                                    --> Qdrant (upsert vectores + payload)
```

### Flujo de busqueda (sincrono)

```
Frontend --> POST /search --> rag-service --> Embedding de la query (Azure OpenAI)
                                         --> Busqueda vectorial (Qdrant, top-k)
                                         --> Prefiltrado por metadatos (MetadataFilters)
                                         --> Construccion de prompt + GPT-4o-mini
                                         --> Candidatos con score, resumen y motivo
```

---

## Stack tecnologico

| Capa | Tecnologia | Version |
|------|-----------|---------|
| API | FastAPI + uvicorn | 0.136.1 / 0.46.0 |
| Orquestacion RAG | LlamaIndex | 0.14.21 (pinned) |
| Base de datos vectorial | Qdrant | v1.17.1 (imagen Docker) |
| Cliente Qdrant | qdrant-client | 1.18.0 |
| Embeddings | Azure OpenAI `text-embedding-3-small` | dim 1536 |
| Chat / LLM | Azure OpenAI `gpt-4o-mini` | |
| Almacenamiento de PDFs | Azure Blob Storage | azure-storage-blob 12.28.0 |
| Extraccion de texto PDF | PyMuPDF + pypdf | 1.27.2.3 / 6.11.0 |
| Secretos | Azure Key Vault | azure-keyvault-secrets 4.11.0 |
| Autenticacion Azure | DefaultAzureCredential (Managed Identity) | azure-identity 1.25.3 |
| Configuracion | pydantic-settings | 2.14.1 |
| Logging estructurado | structlog | 25.5.0 |
| HTTP interno async | httpx / aiohttp | |
| Empaquetado | Docker / Docker Compose | |

---

## Estructura del proyecto

```
TalentRefineAI-Backend/
+-- app/
|   +-- shared/
|   |   +-- config.py             # Settings globales + cliente Key Vault
|   |   +-- models.py             # CandidatePayload, ApiResponse, DocumentStatus
|   |   +-- exceptions.py         # Excepciones de dominio
|   |   +-- fakes.py              # fake_embedding() SHA-256 + local_blob_upload/download()
|   |   `-- logging.py            # Configuracion structlog compartida
|   |
|   +-- ingest_service/
|   |   +-- main.py
|   |   +-- Dockerfile
|   |   +-- routers/              # POST /upload   GET /upload/{id}/status
|   |   +-- services/             # Validacion, encolado y background task
|   |   +-- repositories/         # Acceso a Blob Storage (lazy _get_client)
|   |   `-- integrations/         # Cliente Azure Blob
|   |
|   +-- processing_service/
|   |   +-- main.py
|   |   +-- Dockerfile
|   |   +-- routers/              # POST /processing/{doc_id}
|   |   +-- services/
|   |   |   `-- indexing_service.py  # Pipeline PDF -> chunking -> embedding -> Qdrant
|   |   +-- repositories/         # Blob (descarga) + Qdrant (escritura)
|   |   `-- integrations/         # Cliente Azure OpenAI (embeddings)
|   |
|   `-- rag_service/
|       +-- main.py
|       +-- Dockerfile
|       +-- routers/              # POST /search + schemas Pydantic
|       +-- services/
|       |   `-- rag_engine.py     # RAGEngine: LlamaIndex + MetadataFilters + GPT-4o-mini
|       +-- repositories/         # Qdrant (lectura / busqueda vectorial)
|       `-- integrations/         # Cliente Azure OpenAI (embedding + chat)
|
+-- requirements/
|   +-- ingest.txt                # Deps del ingest-service (pinned)
|   +-- processing.txt            # Deps del processing-service (pinned, llama-index)
|   `-- rag.txt                   # Deps del rag-service (pinned, llama-index + llms)
|
+-- tests/
|   +-- unit/
|   |   `-- test_unit.py          # 28 tests unitarios (sin Docker, sin Azure)
|   `-- integration/
|       `-- test_local_fake_flow.py  # 7 tests E2E contra stack Docker fake
|
+-- docker-compose.yml            # Stack base (Azure real o local con override)
+-- docker-compose.local-fake.yml # Override: modo local fake sin Azure
+-- Makefile                      # Targets de desarrollo y operacion
+-- pyproject.toml                # Dependencias de desarrollo y herramientas
+-- AZURE_INTEGRATION_PLAN.md     # Plan tecnico de integracion con Azure
`-- LOCAL_FAKE_BACKEND_FLOW_TESTING.md  # Guia del modo local fake
```

---

## Modo de operacion: fake local vs Azure real

| Variable | Modo fake (`local_fake`) | Modo Azure real |
|----------|--------------------------|-----------------|
| `USE_FAKE_INTEGRATIONS` | `true` | `false` |
| `APP_ENV` | `local_fake` | `development` / `production` |
| Azure Blob Storage | Disco local (`/tmp/talentrefine-blobs`) | Azure Storage Account |
| Azure OpenAI embeddings | SHA-256 determinista, dim 1536 | `text-embedding-3-small` real |
| Azure OpenAI chat | Resumen/justificacion local | GPT-4o-mini real |
| Azure Key Vault | No se consulta (`get_secret` devuelve `""`) | Key Vault real |
| Qdrant | Docker local (`qdrant:6333`) | Container App + Azure File Share |

> El modo fake esta **bloqueado** si `APP_ENV` es `production`, `prod` o `pro`.

---

## Ejecucion local (modo fake, sin Azure)

### Prerrequisitos

- Docker Desktop en ejecucion
- Python 3.12 + venv activo (`.venv/`)

### Levantar el stack fake

```bash
# Nota: excluir streamlit-frontend (repo separado, no presente aqui)
docker-compose -f docker-compose.yml -f docker-compose.local-fake.yml \
  up --build -d qdrant ingest-service processing-service rag-service

# O con Make:
make dev-fake
```

### Verificar servicios

```bash
curl http://localhost:8001/health   # ingest-service
curl http://localhost:8002/health   # processing-service
curl http://localhost:8003/health   # rag-service
```

### Ejecutar tests

```bash
# Tests unitarios (no requieren Docker)
.venv/Scripts/pytest tests/unit/ -v       # 28 tests

# Tests de integracion E2E (requieren stack fake levantado)
.venv/Scripts/pytest tests/integration/ -v  # 7 tests
```

### Parar el stack

```bash
docker-compose -f docker-compose.yml -f docker-compose.local-fake.yml down
```

---

## Contrato de API

Todos los endpoints usan el mismo envelope de respuesta:

```json
{ "data": "...", "error": null }
{ "data": null, "error": "mensaje descriptivo" }
```

### ingest-service (puerto 8001)

| Metodo | Ruta | Status | Descripcion |
|--------|------|--------|-------------|
| `POST` | `/upload` | 202 | Sube uno o varios PDFs (`multipart/form-data`). Procesamiento asincrono. |
| `GET` | `/upload/{id}/status` | 200 / 404 | Consulta el estado de un documento. |

**Respuesta `POST /upload`:**
```json
{
  "data": [{ "id": "uuid", "file_name": "cv.pdf", "status": "pending" }],
  "error": null
}
```

### processing-service (puerto 8002)

| Metodo | Ruta | Status | Descripcion |
|--------|------|--------|-------------|
| `POST` | `/processing/{doc_id}` | 202 | Dispara el pipeline PDF -> Qdrant. **Solo llamado internamente por ingest-service.** |

### rag-service (puerto 8003)

| Metodo | Ruta | Status | Descripcion |
|--------|------|--------|-------------|
| `POST` | `/search` | 200 / 500 | Busqueda RAG con descripcion del puesto y filtros opcionales. |

**Request `POST /search`:**
```json
{
  "job_description": "Ingeniero Python con experiencia en FastAPI",
  "filters": { "min_exp_years": 3, "location": "Madrid" },
  "top_k": 5
}
```

**Respuesta:**
```json
{
  "data": [
    {
      "candidate_id": "uuid",
      "file_name": "cv_nombre.pdf",
      "score": 0.9123,
      "summary": "Candidato con 5 anios de experiencia en...",
      "reason": "Encaja porque domina FastAPI y tiene experiencia en...",
      "contact_info": {},
      "exp_years": 5,
      "location": "Madrid"
    }
  ],
  "error": null
}
```

---

## Modelo de datos en Qdrant

- **Coleccion:** `talentrefine-candidates` (configurable con `QDRANT_COLLECTION`)
- **Vector:** dimension 1536, distancia coseno (`text-embedding-3-small`)

```json
{
  "candidate_id": "uuid-del-documento",
  "file_name": "ruta/blob/en/storage",
  "contact_info": {},
  "exp_years": 0,
  "location": null,
  "chunk_text": "fragmento del CV..."
}
```

> `contact_info`, `exp_years` y `location` se persisten vacios actualmente. El enriquecimiento NLP esta pendiente.

---

## Variables de entorno

| Variable | Descripcion | Valor por defecto |
|----------|-------------|-------------------|
| `USE_FAKE_INTEGRATIONS` | Activa modo fake local | `false` |
| `APP_ENV` | Entorno de ejecucion | `development` |
| `KEY_VAULT_URL` | URL del Azure Key Vault | `""` |
| `BLOB_ACCOUNT_URL` | URL de la cuenta de Blob Storage | `""` |
| `BLOB_CONTAINER_NAME` | Nombre del contenedor de blobs | `cv-uploads` |
| `QDRANT_URL` | URL de Qdrant | `http://localhost:6333` |
| `QDRANT_COLLECTION` | Nombre de la coleccion | `talentrefine-candidates` |
| `AZURE_OPENAI_ENDPOINT` | Endpoint del recurso Azure OpenAI | `""` |
| `AZURE_OPENAI_API_VERSION` | Version de la API OpenAI | `2024-02-15-preview` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Deployment de embeddings | `text-embedding-3-small` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Deployment de chat | `gpt-4o-mini` |
| `LOCAL_BLOB_PATH` | Directorio fake para blobs | `/tmp/talentrefine-blobs` |
| `LOG_LEVEL` | Nivel de logging | `INFO` |

---

## Proximos pasos: despliegue y validacion en Azure

Esta seccion describe el camino incremental desde el entorno local fake hasta una validacion completa del backend en Azure. Cada fase deja una evidencia verificable antes de avanzar a la siguiente.

### Fase 0 - Alinear variables de entorno entre Terraform y backend

Antes de cualquier despliegue, verificar que los nombres de variables que Terraform inyecta en los Container Apps coincidan exactamente con los que lee `app/shared/config.py`:

| Backend espera | Terraform debe inyectar |
|----------------|------------------------|
| `KEY_VAULT_URL` | `KEY_VAULT_URL` (no `KEY_VAULT_URI`) |
| `BLOB_ACCOUNT_URL` | `BLOB_ACCOUNT_URL` |
| `AZURE_OPENAI_ENDPOINT` | `AZURE_OPENAI_ENDPOINT` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `AZURE_OPENAI_CHAT_DEPLOYMENT` |
| `QDRANT_URL` | `QDRANT_URL` |
| `USE_FAKE_INTEGRATIONS` | `false` (hardcoded en Terraform para Azure) |

**Criterio de aceptacion:** `terraform plan` no produce advertencias sobre variables no reconocidas. Los tres servicios arrancan sin errores de configuracion.

---

### Fase 1 - Provisionar infraestructura base

Provisionar los recursos Azure usando los modulos Terraform del repositorio de infraestructura:

1. Resource Group y networking
2. Azure Container Registry (ACR)
3. Azure Blob Storage + contenedor `cv-uploads`
4. Azure OpenAI con deployments `text-embedding-3-small` (dim 1536) y `gpt-4o-mini`
5. Qdrant en Azure Container Apps con Azure File Share para persistencia
6. Azure Key Vault con Managed Identity asignada a los tres servicios backend
7. Log Analytics + Application Insights

> **Importante:** La coleccion Qdrant debe crearse **antes** del primer despliegue del backend. La dimension debe ser **1536** para coincidir con `text-embedding-3-small`.

```bash
# Bootstrap de la coleccion Qdrant en Azure (ejecutar una sola vez)
curl -X PUT "https://<qdrant-app>.azurecontainerapps.io/collections/talentrefine-candidates" \
  -H "Content-Type: application/json" \
  -d '{"vectors": {"size": 1536, "distance": "Cosine"}}'

# Verificar
curl "https://<qdrant-app>.azurecontainerapps.io/collections/talentrefine-candidates"
# Esperado: "status": "green"
```

**Criterio de aceptacion:** `GET /collections/talentrefine-candidates` devuelve `"status": "green"`.

---

### Fase 2 - Construir y publicar imagenes en ACR

Cada servicio tiene su propio Dockerfile y requirements pinned:

```bash
az acr login --name <acr-name>

docker build -f app/ingest_service/Dockerfile    -t <acr>.azurecr.io/ingest-service:latest    .
docker build -f app/processing_service/Dockerfile -t <acr>.azurecr.io/processing-service:latest .
docker build -f app/rag_service/Dockerfile        -t <acr>.azurecr.io/rag-service:latest        .

docker push <acr>.azurecr.io/ingest-service:latest
docker push <acr>.azurecr.io/processing-service:latest
docker push <acr>.azurecr.io/rag-service:latest
```

**Criterio de aceptacion:** Las tres imagenes aparecen en el registro ACR.

---

### Fase 3 - Desplegar los tres Container Apps

Terraform debe crear un Container App por servicio. Variables de entorno minimas para entorno `dev`:

```hcl
env_vars = {
  USE_FAKE_INTEGRATIONS              = "false"
  APP_ENV                            = "development"
  QDRANT_URL                         = "http://qdrant.internal:6333"
  BLOB_ACCOUNT_URL                   = "https://<account>.blob.core.windows.net"
  BLOB_CONTAINER_NAME                = "cv-uploads"
  AZURE_OPENAI_ENDPOINT              = "https://<openai-resource>.openai.azure.com/"
  AZURE_OPENAI_API_VERSION           = "2024-02-15-preview"
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT  = "text-embedding-3-small"
  AZURE_OPENAI_CHAT_DEPLOYMENT       = "gpt-4o-mini"
  KEY_VAULT_URL                      = "https://<vault>.vault.azure.net/"
  LOG_LEVEL                          = "INFO"
}
```

> `processing-service` debe configurarse con ingreso **interno** (solo llamado por ingest-service).

**Criterio de aceptacion:** Los tres Container Apps estan en estado `Running`. Los endpoints `/health` devuelven 200.

```bash
curl https://<ingest-fqdn>/health
curl https://<rag-fqdn>/health
# processing-service: verificar via logs que arranco correctamente
```

---

### Fase 4 - Smoke test de Blob Storage

```bash
# Subir PDF de prueba al ingest-service en Azure
curl -X POST "https://<ingest-fqdn>/upload" \
  -F "files=@tests/fixtures/cv_test.pdf"
# Esperado: { "data": [{ "id": "<doc_id>", "status": "pending" }], "error": null }

# Verificar estado (el procesamiento es asincrono, esperar ~10s)
curl "https://<ingest-fqdn>/upload/<doc_id>/status"
# Esperado: { "data": { "status": "processed" }, "error": null }
```

**Criterio de aceptacion:** El blob aparece en el contenedor `cv-uploads` en Azure Storage Explorer. El estado transita de `pending` a `processed`.

---

### Fase 5 - Smoke test de embeddings y Qdrant

```bash
# Verificar que processing-service persisitio vectores en Qdrant
curl "https://<qdrant-app>.azurecontainerapps.io/collections/talentrefine-candidates/points/scroll" \
  -H "Content-Type: application/json" \
  -d '{"limit": 5, "with_payload": true, "with_vector": false}'
```

**Criterio de aceptacion:** Al menos un punto con `candidate_id` igual al `doc_id` del upload aparece en la coleccion.

---

### Fase 6 - Smoke test de busqueda RAG

```bash
curl -X POST "https://<rag-fqdn>/search" \
  -H "Content-Type: application/json" \
  -d '{
    "job_description": "Desarrollador Python con experiencia en APIs REST",
    "filters": {},
    "top_k": 3
  }'
```

**Criterio de aceptacion:** La respuesta contiene al menos un candidato con `score > 0`, `summary` no vacio y `reason` no vacio. Latencia < 5s.

---

### Fase 7 - Alinear frontend y demo E2E

El frontend Streamlit necesita los siguientes ajustes para una demo E2E real:

| Frontend actual (mock) | Backend real |
|------------------------|--------------|
| `/api/v1/documents/upload` | `/upload` |
| `/api/v1/rag/query` | `/search` |
| `/api/v1/documents/status` | `/upload/{doc_id}/status` |

Opciones:
- **Opcion A:** Actualizar el cliente API del frontend para usar los contratos reales.
- **Opcion B:** Introducir Azure API Management que reescriba las rutas.

**Criterio de aceptacion:** El frontend puede subir un PDF y recibir candidatos con scores reales sin mocks activos.

---

### Fase 8 - Observabilidad con Application Insights

```kusto
// Trazar un doc_id a traves de los tres servicios en Log Analytics
traces
| where message contains "<doc_id>"
| project timestamp, cloud_RoleName, message, severityLevel
| order by timestamp asc
```

**Criterio de aceptacion:** Cada evento del flujo (`upload recibido`, `procesamiento disparado`, `chunks generados`, `vectores persistidos`, `busqueda ejecutada`) aparece con el mismo `doc_id` propagado entre los tres servicios.

---

## Riesgos conocidos antes de la integracion Azure

| Riesgo | Impacto | Mitigacion |
|--------|---------|------------|
| Estado de documentos en memoria (ingest) | Se pierde al reiniciar; replicas inconsistentes | Persistir en Azure Table Storage o Cosmos DB antes de escalar |
| Coleccion Qdrant no inicializada en Azure | Upsert falla con 404 | Bootstrap explicito (Fase 1) antes de desplegar servicios |
| Variables Terraform != variables backend | Servicios arrancan sin config valida | Alinear nombres segun `app/shared/config.py` (ver Fase 0) |
| Secretos no creados en Key Vault | `get_secret()` devuelve 404 | Crear secretos via `azurerm_key_vault_secret` en Terraform |
| processing-service con ingreso publico | Endpoint interno expuesto | Configurar `ingress: internal` en Terraform |
| Frontend con `USE_MOCK_DATA=true` en Azure | Demo no valida el backend real | Fijar `USE_MOCK_DATA=false` y alinear rutas API |
| Dimension vectorial incorrecta en Qdrant | Search devuelve resultados invalidos | Verificar dim=1536 al crear la coleccion (Fase 1) |

---

## Cobertura de tests

| Suite | Tests | Requiere Docker | Requiere Azure |
|-------|-------|-----------------|----------------|
| `tests/unit/test_unit.py` | 28 | No | No |
| `tests/integration/test_local_fake_flow.py` | 7 | Si (fake stack) | No |

---

## Comandos Make de referencia

```bash
make dev-fake    # Levanta stack fake Docker (sin Azure)
make dev         # Levanta los 3 servicios localmente con hot-reload (requiere az login)
make ingest      # Solo ingest-service (puerto 8001)
make processing  # Solo processing-service (puerto 8002)
make rag         # Solo rag-service (puerto 8003)
make stop        # Para procesos uvicorn de make dev
make build       # Construye imagenes Docker sin levantar
make down        # Para y elimina contenedores Docker
make logs        # Logs en tiempo real
make ps          # Estado de contenedores
```
